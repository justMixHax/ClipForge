# Speaks each line of a JSON manifest into its own WAV file with the voices built into Windows.
# Used only by make_sample.py to build the synthetic test video. Nothing is downloaded.
param([Parameter(Mandatory = $true)][string]$Manifest)

Add-Type -AssemblyName System.Speech
$lines = Get-Content -Raw -Encoding UTF8 $Manifest | ConvertFrom-Json
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
    22050,
    [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
    [System.Speech.AudioFormat.AudioChannel]::Mono)

$installed = $synth.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }
foreach ($l in $lines) {
    if ($installed -contains $l.voice) { $synth.SelectVoice($l.voice) }
    $synth.Rate = [int]$l.rate
    $synth.Volume = 100
    $synth.SetOutputToWaveFile($l.file, $fmt)
    $synth.Speak($l.text)
    $synth.SetOutputToNull()
}
$synth.Dispose()
Write-Output ("spoke {0} lines" -f $lines.Count)
