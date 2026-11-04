#requires -Version 7.5
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$StudyRoot,
    [Parameter(Mandatory)][ValidateSet('eia', 'fed')][string]$Source,
    [ValidateRange(0, [int]::MaxValue)][int]$Offset = 0,
    [ValidateRange(1, 50)][int]$Limit = 5
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-StrictMode -Version Latest
$Source = $Source.ToLowerInvariant()
$lock = $null
$cacheRoot = $null
$currentJob = $null
$utf8 = [Text.UTF8Encoding]::new($false)

function Read-Json([string]$Path) {
    return Get-Content -LiteralPath $Path -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable -DateKind String
}
function Write-NewJson([string]$Path, $Value) {
    $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try {
        $bytes = $utf8.GetBytes(($Value | ConvertTo-Json -Depth 12 -Compress))
        $stream.Write($bytes, 0, $bytes.Length)
    } finally { $stream.Dispose() }
}
function Get-Key([string]$Url) {
    return [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($utf8.GetBytes($Url))).ToLowerInvariant().Substring(0, 16)
}
function Assert-Url([string]$Url, [bool]$Csv = $false, [string]$PageUrl = '') {
    $uri = $null
    if (-not [Uri]::TryCreate($Url, [UriKind]::Absolute, [ref]$uri) -or
        $uri.Scheme -cne 'https' -or $uri.UserInfo -or $uri.Query -or $uri.Fragment -or -not $uri.IsDefaultPort) {
        throw "Invalid URL: $Url"
    }
    $hostName = if ($Source -eq 'eia') { 'www.eia.gov' } else { 'www.federalreserve.gov' }
    if ($uri.Host -cne $hostName) { throw "Forbidden URL host: $Url" }
    if ($Csv) {
        $directory = ([Uri]$PageUrl).AbsolutePath -replace '[^/]+$', ''
        if (-not $uri.AbsolutePath.StartsWith($directory, [StringComparison]::Ordinal) -or
            -not $uri.AbsolutePath.EndsWith('.csv', [StringComparison]::Ordinal)) { throw "Invalid CSV URL directory: $Url" }
    } elseif ($Source -eq 'eia') {
        if (-not $uri.AbsolutePath.StartsWith('/petroleum/supply/weekly/archive/', [StringComparison]::Ordinal) -or
            -not $uri.AbsolutePath.EndsWith('.php', [StringComparison]::Ordinal)) { throw "Invalid EIA URL path: $Url" }
    } else {
        if ($uri.AbsolutePath -cnotmatch '^/releases/h10/[0-9]{8}/?$') { throw "Invalid Fed URL path: $Url" }
        return $Url.TrimEnd('/') + '/'
    }
    return $Url
}
function Get-Snapshot([string]$Url, [string]$LinkDate, [bool]$CacheOnly = $false) {
    $key = Get-Key $Url
    $body = Join-Path $cacheRoot "$key.body"
    $meta = Join-Path $cacheRoot "$key.meta.json"
    $hasBody = Test-Path -LiteralPath $body
    $hasMeta = Test-Path -LiteralPath $meta
    if ($hasBody -ne $hasMeta) { throw "Orphan cache artifact: $key" }
    if ($hasBody) {
        $m = Read-Json $meta
        foreach ($field in @('retrieved_at', 'last_modified', 'content_type')) {
            if (-not $m.ContainsKey($field) -or ($null -ne $m[$field] -and $m[$field] -isnot [string])) {
                throw "Invalid cache metadata field: $field"
            }
        }
        $retrieved = [DateTimeOffset]::MinValue
        if ($m.retrieved_at -isnot [string] -or $m.retrieved_at -cnotmatch '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})$' -or
            -not [DateTimeOffset]::TryParse($m.retrieved_at, [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::None, [ref]$retrieved)) { throw 'Invalid cache retrieved_at: explicit timezone required' }
        $length = (Get-Item -LiteralPath $body).Length
        if ($m.url -cne $Url -or $m.final_url -cne $Url -or $m.status -ne 200 -or
            $m.source -cne $Source -or $m.link_date -cne $LinkDate -or $m.bytes -ne $length -or
            $length -gt 2MB -or (Get-FileHash -LiteralPath $body -Algorithm SHA256).Hash.ToLowerInvariant() -cne $m.sha256) {
            throw "Modified or inconsistent cache: $key"
        }
        return @{ body_path = $body; metadata_path = $meta; sha256 = $m.sha256; cached = $true; url = $Url }
    }
    if ($CacheOnly) { throw "Completed receipt cache files missing: $key" }
    $timestampPath = Join-Path $cacheRoot 'last_request.json'
    if (Test-Path -LiteralPath $timestampPath) {
        $last = [DateTimeOffset]::Parse((Read-Json $timestampPath).last_request_at, [Globalization.CultureInfo]::InvariantCulture)
        $wait = $interval - ([DateTimeOffset]::UtcNow - $last).TotalSeconds
        if ($wait -gt 0) { Start-Sleep -Milliseconds ([Math]::Ceiling($wait * 1000)) }
    }
    $started = [DateTimeOffset]::UtcNow.ToString('o')
    [IO.File]::WriteAllText($timestampPath, (@{ last_request_at = $started } | ConvertTo-Json -Compress), $utf8)
    $part = "$body.part"
    try {
        $response = Invoke-WebRequest -Uri $Url -PassThru -OutFile $part -TimeoutSec 25 -MaximumRedirection 0
    } finally {
        $ended = [DateTimeOffset]::UtcNow.ToString('o')
        [IO.File]::WriteAllText($timestampPath, (@{ last_request_at = $ended } | ConvertTo-Json -Compress), $utf8)
    }
    $finalUrl = $response.BaseResponse.RequestMessage.RequestUri.AbsoluteUri
    $length = (Get-Item -LiteralPath $part).Length
    if ([int]$response.StatusCode -ne 200 -or $finalUrl -cne $Url -or $length -gt 2MB) {
        throw "Response rejected (status/final URL/2MB bound): $Url"
    }
    $hash = (Get-FileHash -LiteralPath $part -Algorithm SHA256).Hash.ToLowerInvariant()
    $m = @{ url = $Url; final_url = $finalUrl; status = 200; sha256 = $hash; bytes = $length
        retrieved_at = [DateTimeOffset]::UtcNow.ToString('o'); source = $Source; link_date = $LinkDate
        last_modified = ($response.Headers['Last-Modified'] -join ', '); content_type = ($response.Headers['Content-Type'] -join ', ') }
    Write-NewJson "$meta.part" $m
    [IO.File]::Move($part, $body)
    [IO.File]::Move("$meta.part", $meta)
    return @{ body_path = $body; metadata_path = $meta; sha256 = $hash; cached = $false; url = $Url }
}
function Get-PlainText([string]$Html) {
    return ([Net.WebUtility]::HtmlDecode(($Html -replace '(?is)<[^>]+>', ' ')) -replace '\s+', ' ').Trim()
}
function Get-ArchiveReleaseDate([string]$Html) {
    $text = Get-PlainText $Html
    $labels = [regex]::Matches($text, '(?i)Release Date:')
    $dates = [regex]::Matches($text, '(?i)Release Date:\s*([A-Za-z]+\s+\d{1,2},\s*\d{4})(?!\w)')
    if ($labels.Count -ne 1 -or $dates.Count -ne 1) { throw 'Release date missing, malformed or ambiguous' }
    $parsed = [DateTime]::MinValue
    if (-not [DateTime]::TryParseExact($dates[0].Groups[1].Value, 'MMMM d, yyyy', [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::None, [ref]$parsed)) { throw 'Release date malformed' }
    return $parsed.ToString('yyyy-MM-dd')
}
function Get-PriceCsvUrl([string]$Html, [string]$PageUrl) {
    $label = if ($priceKind -ceq 'spot') { 'Spot Prices of Crude Oil' } else { 'NYMEX Futures Prices' }
    $rows = @([regex]::Matches($Html, '(?is)<tr\b[^>]*>.*?</tr>') |
        Where-Object { (Get-PlainText $_.Value) -match ('(?i)\b' + [regex]::Escape($label) + '\b') })
    if ($rows.Count -ne 1) { throw "Expected unique $label row missing" }
    $links = @([regex]::Matches($rows[0].Value, '(?is)\bhref\s*=\s*[''"]([^''"]+)[''"]') |
        Where-Object { [Net.WebUtility]::HtmlDecode($_.Groups[1].Value) -match '(?i)\.csv(?:[?#].*)?$' })
    if ($links.Count -ne 1) { throw 'Expected unique price CSV link missing' }
    $csvUrl = [Uri]::new([Uri]$PageUrl, [Net.WebUtility]::HtmlDecode($links[0].Groups[1].Value)).AbsoluteUri
    return Assert-Url $csvUrl $true $PageUrl
}
function Assert-CompletedReceipt($Job) {
    $jobId = Get-Key $Job.url
    $receiptPath = Join-Path $cacheRoot "receipts/$jobId.completed.json"
    if (-not (Test-Path -LiteralPath $receiptPath)) { return }
    $existing = Read-Json $receiptPath
    if ($existing.source -cne $Source -or $existing.job_url -cne $Job.url -or
        $existing.job_id -cne $jobId -or $existing.receipt_path -cne $receiptPath) { throw 'Existing completed receipt differs; inspect cache' }
    $url = Assert-Url $Job.url
    $expected = @{ page = (Get-Snapshot $url $Job.link_date $true) }
    $cachedHtml = [IO.File]::ReadAllText($expected.page.body_path)
    $release = Get-ArchiveReleaseDate $cachedHtml
    if ($existing.release_date -cne $release -or ($Source -eq 'eia' -and $release -cne $Job.link_date) -or
        ($Source -eq 'fed' -and $existing.archive_index_date -cne $Job.link_date)) { throw 'Existing completed receipt dates differ; inspect cache' }
    if ($Source -eq 'eia') {
        $csvUrl = Get-PriceCsvUrl $cachedHtml $url
        $expected.price_csv = Get-Snapshot $csvUrl $Job.link_date $true
    }
    foreach ($partName in $expected.Keys) {
        foreach ($field in @('sha256', 'body_path', 'metadata_path', 'url')) {
            if ($existing[$partName][$field] -cne $expected[$partName][$field]) { throw 'Existing completed receipt differs; inspect cache' }
        }
    }
}

try {
    $StudyRoot = (Resolve-Path -LiteralPath $StudyRoot).Path
    $freeze = Read-Json (Join-Path $StudyRoot 'scope-freeze.json')
    foreach ($name in @('config.json', 'PREREGISTRATION.md')) {
        $entries = @($freeze.files | Where-Object { $_.name -ceq $name })
        $actual = (Get-FileHash -LiteralPath (Join-Path $StudyRoot $name) -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($entries.Count -ne 1 -or $entries[0].sha256 -cnotmatch '^[0-9a-f]{64}$' -or $actual -cne $entries[0].sha256) {
            throw "Scope freeze mismatch: $name"
        }
    }
    $config = Read-Json (Join-Path $StudyRoot 'config.json')
    $priceKind = if ($config.ContainsKey('eia_price_kind')) { $config.eia_price_kind } else { 'futures' }
    if ($priceKind -isnot [string] -or $priceKind -cnotin @('spot', 'futures')) { throw 'Invalid eia_price_kind' }
    $interval = [Math]::Max(1.25, [double]$config.source_min_interval_seconds)
    if ([double]::IsNaN($interval) -or [double]::IsInfinity($interval)) { throw 'Invalid source interval' }
    $jobDocument = Read-Json (Join-Path $StudyRoot 'archive-jobs.json')
    if (-not $jobDocument.ContainsKey($Source) -or $jobDocument[$Source] -isnot [Array]) { throw 'Invalid source jobs array' }
    $jobs = $jobDocument[$Source]
    foreach ($job in $jobs) {
        if ($job.source -cne $Source -or $job.link_date -cnotmatch '^\d{4}-\d{2}-\d{2}$') { throw 'Invalid source job' }
        $null = [DateTime]::ParseExact($job.link_date, 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
        $null = Assert-Url $job.url
    }
    $cacheRoot = Join-Path $StudyRoot "raw/$Source"
    $selectedJobs = @($jobs | Select-Object -Skip $Offset -First $Limit)
    foreach ($job in $selectedJobs) { Assert-CompletedReceipt $job }
    $null = [IO.Directory]::CreateDirectory($cacheRoot)
    $lock = [IO.File]::Open((Join-Path $cacheRoot '.collector.lock'), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    foreach ($job in $selectedJobs) { Assert-CompletedReceipt $job }
    $receiptRoot = Join-Path $cacheRoot 'receipts'
    $null = [IO.Directory]::CreateDirectory($receiptRoot)
    foreach ($job in $selectedJobs) {
        $currentJob = $job
        if (@(Get-ChildItem -LiteralPath $cacheRoot -Filter '*.part' -File).Count) { throw 'Stale cache .part artifact requires inspection' }
        $url = Assert-Url $job.url
        $page = Get-Snapshot $url $job.link_date
        $html = [IO.File]::ReadAllText($page.body_path)
        $release = Get-ArchiveReleaseDate $html
        if ($Source -eq 'eia' -and $release -cne $job.link_date) { throw "Release date mismatch: $release" }
        $receipt = [ordered]@{ source = $Source; job_id = (Get-Key $job.url); job_url = $job.url; release_date = $release; page = $page }
        $rows = @([regex]::Matches($html, '(?is)<tr\b[^>]*>.*?</tr>'))
        if ($Source -eq 'fed') {
            $receipt.archive_index_date = $job.link_date
            $china = @($rows | Where-Object { (Get-PlainText $_.Value) -match '(?i)\bCHINA\b.*\bYUAN\b' })
            if (-not $china.Count) { throw 'China/YUAN row missing' }
        } else {
            $csvUrl = Get-PriceCsvUrl $html $url
            $receipt.price_csv = Get-Snapshot $csvUrl $job.link_date
        }
        $receiptPath = Join-Path $receiptRoot "$($receipt.job_id).completed.json"
        $receipt.receipt_path = $receiptPath
        if (Test-Path -LiteralPath $receiptPath) {
            Assert-CompletedReceipt $job
        } else { Write-NewJson $receiptPath $receipt }
        $receipt | ConvertTo-Json -Depth 12 -Compress
        $currentJob = $null
    }
} catch {
    $failure = $_
    if ($lock -and $cacheRoot) {
        $failurePath = Join-Path $cacheRoot "$([Guid]::NewGuid().ToString('N')).failed.json"
        Write-NewJson $failurePath @{ source = $Source; job = $currentJob; error = $failure.ToString(); failed_at = [DateTimeOffset]::UtcNow.ToString('o') }
    }
    Write-Error -Message $failure.ToString() -ErrorAction Continue
    exit 1
} finally { if ($lock) { $lock.Dispose() } }
