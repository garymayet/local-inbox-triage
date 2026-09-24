<#
    validate-local-model.ps1  -  PASO PREVIO del triaje: valida que modelo local puede
    correr en ESTA maquina, con medicion real (no estimacion).

    Hace dos cosas:
      1) Comprueba el encaje: RAM disponible vs tamano del modelo (con overhead de contexto).
      2) Mide rendimiento real con llama-bench: pp512 (procesar prompt) y tg128 (generar),
         en tokens/s, por cada modelo GGUF encontrado.

    Salida: tabla rankeada + recomendacion.

    Uso:
        powershell -NoProfile -ExecutionPolicy Bypass -File validate-local-model.ps1
        powershell ... -File validate-local-model.ps1 -ModelsDir "D:\modelos" -Threads 4

    No instala nada ni modifica nada. Solo lee y ejecuta llama-bench.
#>
param(
    [string]$ModelsDir = "C:\ProgramData\llm",
    [string]$LlamaDir  = "C:\Tools\llama.cpp",
    [int]$Threads      = 0,
    [int]$Reps         = 3,
    [int]$PromptTokens = 512,
    [int]$GenTokens    = 128
)

$ErrorActionPreference = "Continue"
function ToGB($b) { [math]::Round($b / 1GB, 2) }

# ---------- 1) HARDWARE ----------
$os  = Get-CimInstance Win32_OperatingSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$ramTotalGB = [math]::Round($os.TotalVisibleMemorySize / 1MB, 2)
$ramFreeGB  = [math]::Round($os.FreePhysicalMemory / 1MB, 2)
if ($Threads -le 0) { $Threads = $cpu.NumberOfLogicalProcessors }

Write-Output "==================================================================="
Write-Output " VALIDACION DE MODELO LOCAL  -  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Output "==================================================================="
Write-Output ("  CPU        : " + $cpu.Name)
Write-Output ("  Nucleos    : " + $cpu.NumberOfCores + " fisicos / " + $cpu.NumberOfLogicalProcessors + " logicos (usando " + $Threads + " hilos)")
Write-Output ("  RAM        : " + $ramFreeGB + " GB libres de " + $ramTotalGB + " GB")
$gpu = $null
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) { $gpu = nvidia-smi --query-gpu=name,memory.total --format=csv,noheader }
Write-Output ("  GPU        : " + $(if ($gpu) { $gpu } else { "ninguna -> inferencia en CPU" }))
Write-Output ""

# ---------- 2) HERRAMIENTA ----------
$bench = Join-Path $LlamaDir "llama-bench.exe"
if (-not (Test-Path $bench)) {
    Write-Output "ERROR: no encuentro llama-bench.exe en $LlamaDir"
    Write-Output "Descarga los binarios CPU de llama.cpp y descomprimelos ahi."
    exit 1
}

# ---------- 3) MODELOS ----------
$models = Get-ChildItem $ModelsDir -Filter "*.gguf" -File -ErrorAction SilentlyContinue | Sort-Object Length
if (-not $models) { Write-Output "No hay ficheros .gguf en $ModelsDir"; exit 1 }

# La familia de embeddings no se mide igual (no genera texto): la separamos por nombre.
$embedLike = "bge|e5|minilm|gte|nomic|embed"

Write-Output "Modelos encontrados: $($models.Count)"
Write-Output ""
Write-Output ("Modelo".PadRight(42) + "Peso".PadLeft(10) + "Necesita".PadLeft(12) + "   " + "Encaje en RAM")
Write-Output ("-" * 84)
$encaje = @{}
foreach ($m in $models) {
    $need = [math]::Round(($m.Length / 1GB) * 1.25, 2)   # +25% de contexto/KV/runtime
    if ($need -le ($ramFreeGB * 0.75)) { $v = "OK (holgado)" }
    elseif ($need -le $ramFreeGB)      { $v = "AJUSTADO" }
    else                               { $v = "NO CABE en RAM libre" }
    $encaje[$m.Name] = $v
    Write-Output ($m.Name.PadRight(42) + ((ToGB $m.Length).ToString() + " GB").PadLeft(10) + ($need.ToString() + " GB").PadLeft(12) + "   " + $v)
}
Write-Output ""

# ---------- 4) MEDICION REAL ----------
Write-Output "==================================================================="
Write-Output " MIDIENDO CON llama-bench (pp$PromptTokens / tg$GenTokens, $Reps repeticiones)"
Write-Output "==================================================================="
Write-Output ""

$result = @()
foreach ($m in $models) {
    if ($m.Name -match $embedLike) {
        Write-Output ("[skip] " + $m.Name + "  (modelo de embeddings: no se mide con llama-bench de generacion)")
        continue
    }
    Write-Output ("--> " + $m.Name)
    $raw = Join-Path $env:TEMP ("bench_" + [IO.Path]::GetFileNameWithoutExtension($m.Name) + ".txt")
    & $bench -m $m.FullName -p $PromptTokens -n $GenTokens -t $Threads -r $Reps 2>&1 | Tee-Object -FilePath $raw | Out-Null

    $pp = $null; $tg = $null
    foreach ($line in (Get-Content $raw -ErrorAction SilentlyContinue)) {
        $l = $line.Trim()
        if ($l -match '^\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(pp\d+)\s*\|\s*([0-9.]+)') {
            if ($matches[6] -eq "pp$PromptTokens") { $pp = [double]$matches[7] }
        }
        if ($l -match '^\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(tg\d+)\s*\|\s*([0-9.]+)') {
            if ($matches[6] -eq "tg$GenTokens") { $tg = [double]$matches[7] }
        }
    }
    if ($pp -or $tg) {
        Write-Output ("    prompt: " + $(if ($pp) { [math]::Round($pp,2).ToString() + " tok/s" } else { "n/d" }) + "    generacion: " + $(if ($tg) { [math]::Round($tg,2).ToString() + " tok/s" } else { "n/d" }))
        $result += [pscustomobject]@{
            Modelo = $m.Name; GB = (ToGB $m.Length); Encaje = $encaje[$m.Name]
            pp = $pp; tg = $tg
        }
    } else {
        Write-Output "    (llama-bench no devolvio datos; salida en $raw)"
    }
}

# ---------- 5) VEREDICTO ----------
Write-Output ""
Write-Output "==================================================================="
Write-Output " RESULTADO"
Write-Output "==================================================================="
if (-not $result) { Write-Output "  Sin mediciones validas."; exit 0 }

$result = $result | Sort-Object -Property @{Expression="tg";Descending=$true}
Write-Output ("Modelo".PadRight(42) + "Peso".PadLeft(9) + "Prompt t/s".PadLeft(14) + "Generar t/s".PadLeft(14) + "   " + "Encaje")
Write-Output ("-" * 100)
foreach ($r in $result) {
    $ppTxt = "n/d"; if ($r.pp) { $ppTxt = [string][math]::Round($r.pp, 1) }
    $tgTxt = "n/d"; if ($r.tg) { $tgTxt = [string][math]::Round($r.tg, 1) }
    Write-Output ($r.Modelo.PadRight(42) + ($r.GB.ToString() + " GB").PadLeft(9) + $ppTxt.PadLeft(14) + $tgTxt.PadLeft(14) + "   " + $r.Encaje)
}

$best = $result | Where-Object { $_.Encaje -notmatch "NO CABE" } | Select-Object -First 1
Write-Output ""
if ($best) {
    Write-Output ("  MEJOR OPCION MEDIDA: " + $best.Modelo)
    Write-Output ("    " + [math]::Round($best.tg,1) + " tok/s generando, " + [math]::Round($best.pp,1) + " tok/s leyendo prompt")
    # Regla practica: el triaje de un correo produce ~100-200 tokens (JSON + resumen)
    $seg = [math]::Round(150 / [math]::Max($best.tg, 0.1), 1)
    Write-Output ("    Estimado por correo (~150 tokens de salida): ~" + $seg + " s")
    Write-Output ""
    Write-Output "  NOTA: esto mide VELOCIDAD, no CALIDAD. La calidad se valida con:"
    Write-Output "        python triage_agent.py --dry-run --limit 10"
    Write-Output "        (con el modelo servido en el endpoint que use config.json)"
} else {
    Write-Output "  NINGUN modelo cabe en la RAM libre actual. Cierra aplicaciones o amplia la RAM."
}
