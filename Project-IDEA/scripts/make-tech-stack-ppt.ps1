$ErrorActionPreference='Stop'
$folder = (Get-ChildItem (Join-Path $PSScriptRoot '..\docs') -Recurse -Filter '*.html' | Where-Object { $_.BaseName.Length -eq 3 } | Select-Object -First 1).DirectoryName
$htmlPath = Join-Path $folder '技术栈.html'
$out = Join-Path $folder '技术栈.pptx'
$html = [System.IO.File]::ReadAllText($htmlPath, [System.Text.Encoding]::UTF8)
$matches = [regex]::Matches($html, '(?s)<section class="slide(?: active)?">(.*?)</section>')
$app = New-Object -ComObject PowerPoint.Application
$app.Visible = -1
$pres = $app.Presentations.Add()
$idx = 0
foreach($m in $matches){
  $idx++
  $x = $m.Groups[1].Value
  $tm = [regex]::Match($x, '(?s)<h[12][^>]*>(.*?)</h[12]>')
  $sm = [regex]::Match($x, '(?s)<p class="subtitle">(.*?)</p>')
  $title = [regex]::Replace($tm.Groups[1].Value, '<.*?>', '')
  $sub = [regex]::Replace($sm.Groups[1].Value, '<.*?>', '')
  $body = [regex]::Replace($x, '(?s)<div class="eyebrow">.*?</div>|<h[12][^>]*>.*?</h[12]>|<p class="subtitle">.*?</p>|<div class="arrow">.*?</div>|<div class="node">|</div>|<div class="card">|<span class="tag">.*?</span>|<span class="done">|<span class="plan">|</span>|<h3>|</h3>|<p>|</p>|<ul>|</ul>|<li>|</li>|<small>|</small>|<br\s*/?>', '')
  $body = [System.Net.WebUtility]::HtmlDecode($body)
  $body = [regex]::Replace($body, '(\r?\n){2,}', "`n")
  $sl = $pres.Slides.Add($idx, 12)
  $sl.Background.Fill.ForeColor.RGB = 0x1F1108
  $t = $sl.Shapes.AddTextbox(1,45,35,850,55); $t.TextFrame.TextRange.Text=$title; $t.TextFrame.TextRange.Font.Name='Microsoft YaHei'; $t.TextFrame.TextRange.Font.Size=28; $t.TextFrame.TextRange.Font.Bold=$true; $t.TextFrame.TextRange.Font.Color.RGB=0xFFE366
  $st = $sl.Shapes.AddTextbox(1,48,100,850,35); $st.TextFrame.TextRange.Text=$sub; $st.TextFrame.TextRange.Font.Name='Microsoft YaHei'; $st.TextFrame.TextRange.Font.Size=16; $st.TextFrame.TextRange.Font.Color.RGB=0xA5D7FF
  $b = $sl.Shapes.AddTextbox(1,55,155,840,330); $b.TextFrame.TextRange.Text=$body; $b.TextFrame.WordWrap=$true; $b.TextFrame.TextRange.Font.Name='Microsoft YaHei'; $b.TextFrame.TextRange.Font.Size=17; $b.TextFrame.TextRange.Font.Color.RGB=0xF1F5FA; $b.TextFrame.TextRange.ParagraphFormat.SpaceAfter=10
  $f = $sl.Shapes.AddTextbox(1,50,500,850,22); $f.TextFrame.TextRange.Text=('Technical Stack  |  '+$idx.ToString()+' / '+$matches.Count); $f.TextFrame.TextRange.Font.Name='Arial'; $f.TextFrame.TextRange.Font.Size=10; $f.TextFrame.TextRange.Font.Color.RGB=0x8CA7C1
}
$pres.SaveAs($out)
$pres.Close()
$app.Quit()
[System.Runtime.Interopservices.Marshal]::ReleaseComObject($pres)|Out-Null
[System.Runtime.Interopservices.Marshal]::ReleaseComObject($app)|Out-Null
Write-Output $out