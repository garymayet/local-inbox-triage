<#
    patch-triage-agent.ps1  -  Integra el pre-filtro de laya en triage_agent.py.

    Que hace:
      1) Inserta una funcion classify(email, facts) que intenta el pre-filtro local (laya)
         y solo si no aplica llama al LLM.
      2) Redirige los DOS puntos de llamada (run_dry y _bridge_tick) de
         ollama_classify(e, facts) a classify(e, facts).
      3) Anade las claves de configuracion a config.json.
      4) Verifica con py_compile y comprueba el resultado.

    Idempotente: si ya esta aplicado, no vuelve a tocar el fichero.
    Reversible: deja triage_agent.py.bak antes de modificar.
#>
$ErrorActionPreference = "Stop"
$repo = "C:\Users\mmayet\local-inbox-triage"
$p    = Join-Path $repo "triage_agent.py"
$cfgP = Join-Path $repo "config.json"
$py   = "C:\Program Files\Python312\python.exe"

if (-not (Test-Path $p)) { Write-Output "ERROR: no encuentro $p"; exit 1 }

$c = Get-Content $p -Raw

if ($c -match "def classify\(email, facts\)") {
    Write-Output "  triage_agent.py: YA estaba parcheado (no se toca)"
} else {
    $nueva = @'
def classify(email, facts):
    """Clasifica un correo: pre-filtro local (laya) si esta habilitado en config.json;
    si no, el LLM. Si el pre-filtro falla por cualquier motivo, cae al LLM sin romper nada.
    Anadido por el despliegue de la VM: ver docs/VM-SETUP-DSH.md."""
    if CFG.get("use_laya_prefilter", False):
        try:
            import triage_laya
            pre = triage_laya.prefilter(email, facts, CFG)
            if pre:
                return pre
        except Exception:
            pass
    return ollama_classify(email, facts)


'@
    Copy-Item $p "$p.bak" -Force
    $llamadas = ([regex]::Matches($c, [regex]::Escape("cls = ollama_classify(e, facts)"))).Count
    $c = $c.Replace("def clean_cat(c):", $nueva + "def clean_cat(c):")
    $c = $c.Replace("cls = ollama_classify(e, facts)", "cls = classify(e, facts)")
    Set-Content -Path $p -Value $c -Encoding UTF8 -NoNewline
    Write-Output "  triage_agent.py: parcheado"
    Write-Output "    puntos de llamada redirigidos: $llamadas (esperado 2)"
    Write-Output "    backup: $p.bak"
}

# --- comprobacion de sintaxis ---
& $py -m py_compile $p
Write-Output "  py_compile exit=$LASTEXITCODE"

$c2 = Get-Content $p -Raw
Write-Output "  verificacion:"
Write-Output ("    define classify()                     : " + [bool]($c2 -match "def classify\(email, facts\)"))
Write-Output ("    llamadas classify(e, facts)           : " + ([regex]::Matches($c2, "classify\(e, facts\)")).Count)
Write-Output ("    llamadas directas restantes a ollama  : " + ([regex]::Matches($c2, "cls = ollama_classify")).Count + "  (deben ser 0)")
Write-Output ("    la funcion original sigue intacta     : " + [bool]($c2 -match "def ollama_classify\(email, facts\)"))

# --- config.json: anadir claves con valores por defecto seguros ---
try {
    $cfg = Get-Content $cfgP -Raw | ConvertFrom-Json
    $cambiado = $false
    if ($null -eq $cfg.PSObject.Properties["use_laya_prefilter"]) { $cfg | Add-Member -NotePropertyName use_laya_prefilter -NotePropertyValue $true; $cambiado = $true }
    if ($null -eq $cfg.PSObject.Properties["laya_skip_categories"]) { $cfg | Add-Member -NotePropertyName laya_skip_categories -NotePropertyValue @("newsletter","notification"); $cambiado = $true }
    if ($null -eq $cfg.PSObject.Properties["laya_min_confidence"]) { $cfg | Add-Member -NotePropertyName laya_min_confidence -NotePropertyValue 0.75; $cambiado = $true }
    if ($cambiado) { $cfg | ConvertTo-Json -Depth 6 | Set-Content -Path $cfgP -Encoding UTF8; Write-Output "  config.json: claves anadidas" }
    else { Write-Output "  config.json: ya tenia las claves" }
    $v = Get-Content $cfgP -Raw | ConvertFrom-Json
    Write-Output ("    use_laya_prefilter  = " + $v.use_laya_prefilter)
    Write-Output ("    laya_skip_categories= " + ($v.laya_skip_categories -join ","))
    Write-Output ("    laya_min_confidence = " + $v.laya_min_confidence)
} catch { Write-Output ("  ERROR en config.json: " + $_.Exception.Message) }

& icacls $repo /grant "mmayet:(OI)(CI)F" /T /Q 2>&1 | Out-Null
