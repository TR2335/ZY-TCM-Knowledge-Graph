# 本草通 2.0（tcm-kg）Windows PowerShell 任务脚本（与 Makefile 同名目标等价，R7 缓解）
# 用法：.\scripts\tasks.ps1 <target>   （如 .\scripts\tasks.ps1 check）
param(
    [Parameter(Position = 0)]
    [string]$Target = "help"
)

$ErrorActionPreference = "Stop"
$Neo4jPassword = if ($env:NEO4J_PASSWORD) { $env:NEO4J_PASSWORD } else { "tcmkg-dev-password" }

function Invoke-InBackend([scriptblock]$Block) {
    Push-Location (Join-Path $PSScriptRoot "..\backend")
    try { & $Block } finally { Pop-Location }
}

switch ($Target.ToLower()) {
    "up" { docker compose up -d }
    "up-observability" { docker compose --profile observability up -d }
    "down" { docker compose down }
    "logs" { docker compose logs -f neo4j }
    "neo4j-shell" { docker exec -it tcm-kg-neo4j cypher-shell -u neo4j -p $Neo4jPassword }
    "init-db" {
        Get-Content (Join-Path $PSScriptRoot "init_constraints.cypher") -Raw |
            docker exec -i tcm-kg-neo4j cypher-shell -u neo4j -p $Neo4jPassword
    }
    "check" {
        Invoke-InBackend { uv run ruff check src tests scripts; if ($LASTEXITCODE -ne 0) { exit 1 } }
        Invoke-InBackend { uv run ruff format --check src tests scripts; if ($LASTEXITCODE -ne 0) { exit 1 } }
        Invoke-InBackend { uv run mypy src tests scripts; if ($LASTEXITCODE -ne 0) { exit 1 } }
        Invoke-InBackend { uv run pytest; if ($LASTEXITCODE -ne 0) { exit 1 } }
    }
    "lint" {
        Invoke-InBackend { uv run ruff check src tests scripts; if ($LASTEXITCODE -ne 0) { exit 1 } }
        Invoke-InBackend { uv run ruff format --check src tests scripts; if ($LASTEXITCODE -ne 0) { exit 1 } }
    }
    "fmt" {
        Invoke-InBackend { uv run ruff format src tests scripts }
        Invoke-InBackend { uv run ruff check --fix src tests scripts }
    }
    "typecheck" { Invoke-InBackend { uv run mypy src tests scripts } }
    "test" { Invoke-InBackend { uv run pytest } }
    "sync" { Invoke-InBackend { uv sync } }
    "schema-export" { Invoke-InBackend { uv run python scripts/export_ontology_schema.py } }
    default {
        Write-Host "tcm-kg tasks（与 Makefile 同名目标等价）："
        Write-Host "  up / up-observability / down / logs / neo4j-shell / init-db"
        Write-Host "  check / lint / fmt / typecheck / test / sync / schema-export"
    }
}
