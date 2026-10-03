# Developer build only. The resulting MarketLens.exe needs no installed Python or Node.
param([string]$Python = 'python', [string]$Output = '')
$ErrorActionPreference = 'Stop'
$mlRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
if (-not $Output) { $Output = Join-Path $mlRoot 'portable' }
if (-not (Test-Path "$mlRoot\frontend\dist\index.html")) { throw 'Build frontend/dist before packaging.' }
# Render the existing favicon's circle and sparkline into a Windows application icon.
Add-Type -AssemblyName System.Drawing
$mlIcon = New-Object System.Drawing.Bitmap 256,256
$mlCanvas = [System.Drawing.Graphics]::FromImage($mlIcon)
$mlCanvas.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$mlCanvas.Clear([System.Drawing.Color]::Transparent)
$mlFill = New-Object System.Drawing.SolidBrush ([System.Drawing.ColorTranslator]::FromHtml('#0d1220'))
$mlRing = New-Object System.Drawing.Pen ([System.Drawing.ColorTranslator]::FromHtml('#9f8fec')),20
$mlLine = New-Object System.Drawing.Pen ([System.Drawing.ColorTranslator]::FromHtml('#5fe0bd')),18
$mlLine.StartCap = $mlLine.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
$mlLine.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
$mlCanvas.FillEllipse($mlFill,24,24,208,208)
$mlCanvas.DrawEllipse($mlRing,24,24,208,208)
$mlCanvas.DrawLines($mlLine,[System.Drawing.PointF[]]@((New-Object System.Drawing.PointF 68,152),(New-Object System.Drawing.PointF 100,120),(New-Object System.Drawing.PointF 124,142),(New-Object System.Drawing.PointF 155,96),(New-Object System.Drawing.PointF 188,128)))
$mlPng = New-Object System.IO.MemoryStream
$mlIcon.Save($mlPng,[System.Drawing.Imaging.ImageFormat]::Png)
$mlBytes = $mlPng.ToArray()
$mlIconFile = [System.IO.File]::Create("$mlRoot\backend\packaging\MarketLens.ico")
$mlWriter = New-Object System.IO.BinaryWriter $mlIconFile
$mlWriter.Write([uint16]0); $mlWriter.Write([uint16]1); $mlWriter.Write([uint16]1)
$mlWriter.Write([byte]0); $mlWriter.Write([byte]0); $mlWriter.Write([byte]0); $mlWriter.Write([byte]0)
$mlWriter.Write([uint16]1); $mlWriter.Write([uint16]32); $mlWriter.Write([uint32]$mlBytes.Length); $mlWriter.Write([uint32]22); $mlWriter.Write($mlBytes)
$mlWriter.Dispose(); $mlPng.Dispose(); $mlCanvas.Dispose(); $mlIcon.Dispose(); $mlFill.Dispose(); $mlRing.Dispose(); $mlLine.Dispose()
& $Python -m PyInstaller --noconfirm --distpath $Output --workpath "$mlRoot\backend\build\portable" "$mlRoot\backend\packaging\MarketLens.spec"
if ($LASTEXITCODE -ne 0) { throw 'Executable build failed.' }
