param([Parameter(Mandatory=$true)][string]$ImagePath)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$ImagePath = (Resolve-Path -LiteralPath $ImagePath).ProviderPath
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$taskMethod = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and
    $_.GetGenericArguments().Length -eq 1 -and $_.GetParameters().Length -eq 1
} | Select-Object -First 1
function Await-Result($Operation, [Type]$ResultType) {
    $task = $taskMethod.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    if (-not $task.Wait(10000)) { throw 'Windows OCR operation timed out' }
    return $task.Result
}
$fileType = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$streamType = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
$decoderType = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$bitmapType = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$resultType = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType=WindowsRuntime]
$file = Await-Result ($fileType::GetFileFromPathAsync($ImagePath)) $fileType
$stream = Await-Result ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) $streamType
try {
    $decoder = Await-Result ($decoderType::CreateAsync($stream)) $decoderType
    $bitmap = Await-Result ($decoder.GetSoftwareBitmapAsync()) $bitmapType
    try {
        $language = [Windows.Globalization.Language, Windows.Globalization, ContentType=WindowsRuntime]::new('en-US')
        $engine = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]::TryCreateFromLanguage($language)
        if ($null -eq $engine) {
            $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
        }
        if ($null -eq $engine) { throw 'No Windows OCR language is available' }
        $result = Await-Result ($engine.RecognizeAsync($bitmap)) $resultType
        $lines = @($result.Lines | ForEach-Object {
            @{text=$_.Text; words=@($_.Words | ForEach-Object {
                @{text=$_.Text; x=$_.BoundingRect.X; y=$_.BoundingRect.Y;
                  width=$_.BoundingRect.Width; height=$_.BoundingRect.Height}
            })}
        })
        @{lines=$lines; width=$bitmap.PixelWidth; height=$bitmap.PixelHeight;
          engine='Windows.Media.Ocr'; language=$engine.RecognizerLanguage.LanguageTag} | ConvertTo-Json -Depth 6 -Compress
    } finally { $bitmap.Dispose() }
} finally { $stream.Dispose() }
