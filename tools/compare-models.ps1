<#
    compare-models.ps1  -  Compara varios modelos locales con el MISMO correo y el prompt
    real del agente, y produce una tabla ordenada. Es la respuesta con evidencia a
    "¿Qwen o Gemma? ¿3B o 4B?".

    Para cada modelo: carga, clasifica, mide latencia, valida el JSON y comprueba si la
    categoria devuelta es la esperada (factura duplicada de cliente -> finance).

    Uso:
        powershell -NoProfile -ExecutionPolicy Bypass -File compare-models.ps1
        powershell ... -File compare-models.ps1 -Models "Qwen3-4B-Q4_K_M.gguf","gemma-3-4b-it-Q4_K_M.gguf"

    NO toca el servicio LLMChat: usa el puerto 8090 y lo libera al terminar.
#>
param(
    [string[]]$Models = @(
        "Qwen2.5-3B-Instruct-Q4_K_M.gguf",
        "Qwen3-4B-Q4_K_M.gguf",
        "gemma-3-4b-it-Q4_K_M.gguf",
        "Llama-3.2-3B-Instruct-Q4_K_M.gguf"
    ),
    [string]$ModelsDir = "C:\ProgramData\llm",
    [string]$TriageDir = "C:\Users\mmayet\local-inbox-triage",
    [int]$Port = 8090
)

$ErrorActionPreference = "Continue"
$bench = Join-Path $TriageDir "tools\bench-one-email.ps1"
if (-not (Test-Path $bench)) { Write-Output "ERROR: no encuentro $bench"; exit 1 }

$filas = @()
foreach ($m in $Models) {
    $ruta = Join-Path $ModelsDir $m
    if (-not (Test-Path $ruta)) { Write-Output ("[skip] no existe " + $m); continue }

    Write-Output ("=== Midiendo " + $m + " ===")
    $salida = & $bench -Model $ruta -Port $Port 2>&1 | ForEach-Object { $_ -replace "[^\x20-\x7E]", "" }

    $lat = $null; $tps = $null; $carga = $null; $cat = $null; $jsonOk = $false; $tokOut = $null
    foreach ($l in $salida) {
        if ($l -match "Carga del modelo\s*:\s*([0-9.]+)") { $carga = [double]$matches[1] }
        if ($l -match "Latencia total\s*:\s*([0-9.]+)") { $lat = [double]$matches[1] }
        if ($l -match "Generacion\s*:\s*([0-9.]+)") { $tps = [double]$matches[1] }
        if ($l -match "prompt (\d+) tok \+ salida (\d+) tok") { $tokOut = [int]$matches[2] }
        if ($l -match "category\s*:\s*(\S+)") { $cat = $matches[1] }
        if ($l -match "JSON parseado correctamente") { $jsonOk = $true }
    }

    $veredicto = "?"
    if ($jsonOk -and $cat -eq "finance") { $veredicto = "CORRECTA" }
    elseif ($jsonOk) { $veredicto = "JSON ok, categoria=$cat" }
    else { $veredicto = "JSON INVALIDO" }

    $filas += [pscustomobject]@{
        Modelo = ($m -replace "-Q4_K_M\.gguf", "")
        Carga = $carga; Latencia = $lat; Toks = $tps; Salida = $tokOut
        Categoria = $cat; Json = $jsonOk; Veredicto = $veredicto
    }
    Write-Output ("    carga " + $carga + "s | latencia " + $lat + "s | " + $tps + " tok/s | " + $veredicto)
}

Write-Output ""
Write-Output "==================================================================="
Write-Output " COMPARATIVA (mismo correo, mismo prompt, misma maquina)"
Write-Output "==================================================================="
Write-Output ("Modelo".PadRight(30) + "Carga".PadLeft(8) + "Latencia".PadLeft(10) + "tok/s".PadLeft(8) + "Salida".PadLeft(8) + "   Veredicto")
Write-Output ("-" * 100)
foreach ($f in ($filas | Sort-Object Latencia)) {
    $latT = "n/d"; if ($f.Latencia) { $latT = [string]$f.Latencia + "s" }
    $tpsT = "n/d"; if ($f.Toks) { $tpsT = [string]$f.Toks }
    Write-Output ($f.Modelo.PadRight(30) + ([string]$f.Carga).PadLeft(8) + $latT.PadLeft(10) + $tpsT.PadLeft(8) + ([string]$f.Salida).PadLeft(8) + "   " + $f.Veredicto)
}
Write-Output ""
Write-Output "Criterio: un modelo sirve si (a) devuelve JSON valido y (b) clasifica bien."
Write-Output "Entre los que empatan en calidad, gana el de menor latencia."
Write-Output "Pon el ganador en C:\ProgramData\run-chat.ps1 y reinicia la tarea LLMChat."
