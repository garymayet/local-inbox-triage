<#
    llm-fit.ps1  -  Puerto a Windows de llm-fit.py
    Estima que tamano de modelo LLM local cabe en ESTA maquina, con mediciones reales.
    Sin dependencias: solo PowerShell 5.1. NO instala ni modifica nada (solo lectura).

    Uso:  powershell -NoProfile -ExecutionPolicy Bypass -File llm-fit.ps1
#>
$ErrorActionPreference = "SilentlyContinue"

function ToGB($b) { return [math]::Round($b / 1GB, 2) }

$cs  = Get-CimInstance Win32_ComputerSystem
$os  = Get-CimInstance Win32_OperatingSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1

Write-Output "=================== HARDWARE ==================="
Write-Output ("  CPU         : " + $cpu.Name)
Write-Output ("  Nucleos     : " + $cpu.NumberOfCores + " fisicos / " + $cpu.NumberOfLogicalProcessors + " logicos")
Write-Output ("  RAM total   : " + (ToGB ($os.TotalVisibleMemorySize * 1KB)) + " GB")
Write-Output ("  RAM libre   : " + (ToGB ($os.FreePhysicalMemory * 1KB)) + " GB")

# --- GPU ---
Write-Output ""
$cuda = @()
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    foreach ($l in (nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits)) {
        $p = $l -split ","
        $cuda += [pscustomobject]@{ N = $p[0].Trim(); MB = [int]$p[1] }
    }
}
if ($cuda.Count -gt 0) {
    foreach ($g in $cuda) { Write-Output ("  GPU CUDA    : " + $g.N + "   " + [math]::Round($g.MB / 1024, 1) + " GB VRAM  <-- usable para inferencia") }
} else {
    Write-Output "  GPU CUDA    : ninguna (no hay nvidia-smi) -> inferencia en CPU"
    foreach ($v in (Get-CimInstance Win32_VideoController | Select-Object -First 2)) {
        Write-Output ("  GPU display : " + $v.Name + "  [NO sirve para inferencia]")
    }
}

# --- Disco ---
foreach ($vol in (Get-Volume | Where-Object { $_.DriveLetter -eq "C" })) {
    Write-Output ("  Disco C:    : " + (ToGB $vol.Size) + " GB total, " + (ToGB $vol.SizeRemaining) + " GB libres")
}

# --- Presion de memoria real ---
Write-Output ""
Write-Output "=================== PRESION DE MEMORIA ==================="
$commit = (Get-Counter '\Memory\Committed Bytes').CounterSamples[0].CookedValue
$climit = (Get-Counter '\Memory\Commit Limit').CounterSamples[0].CookedValue
Write-Output ("  Commit      : " + (ToGB $commit) + " GB comprometidos de " + (ToGB $climit) + " GB de limite")
foreach ($p in (Get-CimInstance Win32_PageFileUsage)) {
    Write-Output ("  Pagefile    : " + $p.Name + "  uso actual " + $p.CurrentUsage + " MB  (pico " + $p.PeakUsage + " MB de " + $p.AllocatedBaseSize + " MB)")
}
if ($commit -gt ($os.TotalVisibleMemorySize * 1KB)) {
    Write-Output "  AVISO       : el commit supera la RAM fisica -> la maquina YA esta paginando"
}

Write-Output ""
Write-Output "=================== TOP 8 CONSUMIDORES DE RAM ==================="
Get-Process | Group-Object Name | ForEach-Object {
    [pscustomobject]@{ N = $_.Name; MB = [math]::Round((($_.Group | Measure-Object WorkingSet64 -Sum).Sum) / 1MB) }
} | Sort-Object MB -Descending | Select-Object -First 8 | ForEach-Object {
    Write-Output ("  " + $_.N.PadRight(28) + $_.MB.ToString().PadLeft(6) + " MB")
}

# --- Presupuesto ---
$freeGB = [math]::Round($os.FreePhysicalMemory / 1MB, 2)
$budget = [math]::Round($freeGB * 0.70, 2)

Write-Output ""
Write-Output "=================== PRESUPUESTO PARA EL MODELO ==================="
Write-Output ("  RAM libre ahora                              : " + $freeGB + " GB")
Write-Output ("  Presupuesto utilizable (70% de lo libre)      : " + $budget + " GB")
Write-Output "  (el 30% restante es margen para KV cache, contexto y runtime)"

Write-Output ""
Write-Output "=================== TAMANO MAXIMO TEORICO ==================="
$quant = [ordered]@{ "Q4_K_M" = 0.58; "Q5_K_M" = 0.70; "Q6_K" = 0.82; "Q8_0" = 1.06; "FP16" = 2.00 }
foreach ($q in $quant.Keys) {
    $maxP = $budget / ($quant[$q] * 1.20)
    Write-Output ("  " + $q.PadRight(8) + ("max " + [math]::Round($maxP, 2) + " B params").PadLeft(26))
}

# --- Candidatos concretos (pesos Q4_K_M aproximados) ---
$cands = @(
    [pscustomobject]@{ M = "qwen2.5:0.5b";  G = 0.4 },
    [pscustomobject]@{ M = "llama3.2:1b";   G = 0.8 },
    [pscustomobject]@{ M = "qwen2.5:1.5b";  G = 1.0 },
    [pscustomobject]@{ M = "gemma2:2b";     G = 1.7 },
    [pscustomobject]@{ M = "llama3.2:3b";   G = 2.0 },
    [pscustomobject]@{ M = "qwen2.5:3b";    G = 1.9 },
    [pscustomobject]@{ M = "phi3.5:3.8b";   G = 2.2 },
    [pscustomobject]@{ M = "qwen2.5:7b";    G = 4.7 },
    [pscustomobject]@{ M = "llama3.1:8b";   G = 4.9 },
    [pscustomobject]@{ M = "gemma3:12b";    G = 8.1 }
)
Write-Output ""
Write-Output "=================== CANDIDATOS (pesos Q4 + 25% overhead) ==================="
Write-Output ("  " + "Modelo".PadRight(18) + "Pesos".PadLeft(8) + "  Necesita".PadLeft(11) + "   Veredicto")
foreach ($c in $cands) {
    $need = [math]::Round($c.G * 1.25, 2)
    if ($need -le $budget) {
        $v = "CABE ahora"
    } elseif ($need -le ($freeGB * 1.8)) {
        $v = "solo cerrando apps"
    } else {
        $v = "NO cabe: falta RAM"
    }
    Write-Output ("  " + $c.M.PadRight(18) + ($c.G.ToString() + " GB").PadLeft(8) + ("  " + $need + " GB").PadLeft(11) + "   " + $v)
}
Write-Output ""
Write-Output "NOTA 1: los pesos son aproximados; confirma el tag real en la libreria de Ollama."
Write-Output "NOTA 2: la velocidad depende del vCPU. Con 2 vCPU, un 3B en CPU da ~3-10 tok/s."
Write-Output "NOTA 3: si el veredicto es negativo, las salidas son mas RAM (resize de la VM)"
Write-Output "        o mover la inferencia a otra maquina."
