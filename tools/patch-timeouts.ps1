<#
    patch-timeouts.ps1  -  Hace configurables los timeouts HTTP del agente.

    Problema medido: triage_agent.py usa timeout=120 s para la clasificacion, pero en esta VM
    (1 core fisico, ~1.4 tok/s) Gemma-3-4B tarda 73 s de media y hasta 143 s. Resultado:
    ReadTimeout en todos los correos y el agente no clasifica nada.

    Este parche sustituye los valores fijos por claves de config.json con valores generosos.
    Idempotente y con backup.
#>
$ErrorActionPreference = "Stop"
$repo = "C:\Users\mmayet\local-inbox-triage"
$p    = Join-Path $repo "triage_agent.py"
$cfgP = Join-Path $repo "config.json"
$py   = "C:\Program Files\Python312\python.exe"

$c = Get-Content $p -Raw

if ($c -match 'CFG\.get\("llm_timeout"') {
    Write-Output "  timeouts: YA estaban parametrizados"
} else {
    Copy-Item $p "$p.bak2" -Force
    $n120 = ([regex]::Matches($c, [regex]::Escape("timeout=120"))).Count
    $n180 = ([regex]::Matches($c, [regex]::Escape("timeout=180"))).Count
    $n90  = ([regex]::Matches($c, [regex]::Escape("timeout=90"))).Count

    $c = $c.Replace("timeout=120,", 'timeout=CFG.get("llm_timeout", 600),')
    $c = $c.Replace("timeout=180,", 'timeout=CFG.get("llm_timeout_draft", 900),')
    $c = $c.Replace("timeout=90,",  'timeout=CFG.get("embed_timeout", 180),')

    Set-Content -Path $p -Value $c -Encoding UTF8 -NoNewline
    Write-Output "  timeouts parametrizados:"
    Write-Output "    timeout=120 -> llm_timeout (clasificacion)   : $n120 ocurrencia(s)"
    Write-Output "    timeout=180 -> llm_timeout_draft (borradores): $n180 ocurrencia(s)"
    Write-Output "    timeout=90  -> embed_timeout (embeddings)    : $n90 ocurrencia(s)"
    Write-Output "    backup: $p.bak2"
}

& $py -m py_compile $p
Write-Output "  py_compile exit=$LASTEXITCODE"
$c2 = Get-Content $p -Raw
Write-Output "  verificacion:"
Write-Output ("    timeout=120 restantes: " + ([regex]::Matches($c2, [regex]::Escape("timeout=120"))).Count + "  (deben ser 0)")
Write-Output ("    timeout=180 restantes: " + ([regex]::Matches($c2, [regex]::Escape("timeout=180"))).Count + "  (deben ser 0)")
Write-Output ("    usa CFG llm_timeout  : " + [bool]($c2 -match 'CFG\.get\("llm_timeout"'))

# config.json: claves de timeout
$cfg = Get-Content $cfgP -Raw | ConvertFrom-Json
$cambiado = $false
if ($null -eq $cfg.PSObject.Properties["llm_timeout"])       { $cfg | Add-Member -NotePropertyName llm_timeout -NotePropertyValue 600; $cambiado = $true }
if ($null -eq $cfg.PSObject.Properties["llm_timeout_draft"]) { $cfg | Add-Member -NotePropertyName llm_timeout_draft -NotePropertyValue 900; $cambiado = $true }
if ($null -eq $cfg.PSObject.Properties["embed_timeout"])     { $cfg | Add-Member -NotePropertyName embed_timeout -NotePropertyValue 180; $cambiado = $true }
if ($cambiado) { $cfg | ConvertTo-Json -Depth 6 | Set-Content -Path $cfgP -Encoding UTF8; Write-Output "  config.json: timeouts anadidos" }
$v = Get-Content $cfgP -Raw | ConvertFrom-Json
Write-Output ("    llm_timeout       = " + $v.llm_timeout)
Write-Output ("    llm_timeout_draft = " + $v.llm_timeout_draft)
Write-Output ("    embed_timeout     = " + $v.embed_timeout)
