<#
ludodex <-> Playnite bridge.  Run INSIDE Playnite (Extensions > Execute script, or
the toolbox), where $PlayniteApi is available.

  Export Playnite -> JSON (for ludodex import):
     .\playnite_bridge.ps1 -Export -Path C:\path\playnite_games.json
  Import JSON -> Playnite (from `playnite_export.py`; creates + enriches):
     .\playnite_bridge.ps1 -Import -Path C:\path\ludodex_to_playnite.json [-NoCreate]

JSON shape is the canonical record in playnite.py. Copy the file to/from the Deck
(scp / share); point config keys playnite_import_json / playnite_export_json at it.
#>
param(
  [switch]$Export,
  [switch]$Import,
  [Parameter(Mandatory=$true)][string]$Path,
  [switch]$NoCreate
)

# Playnite library-plugin GUID -> ludodex source name (matches playnite.py)
$PLUGINS = @{
  'cb91dfc9-b977-43bf-8e70-55f46e410fab'='steam';   'aebe8b7c-6dc3-4a66-af31-e7375c6b5e9e'='gog'
  '00000002-dbd1-46c6-b5d0-b1ba559d10e4'='epic';    '00000001-ebb2-4eec-abcb-7c89937a42bb'='itch'
  '85dd7072-2f20-4e76-a007-41035e390724'='ea';      'c2f038e5-8b92-4877-91f1-da9094155fc5'='ubisoft'
  'e3c26a3d-d695-4cb7-a769-5ff7612c7edd'='battlenet';'7e4fbb5e-2ae3-48d4-8ba0-6b30e7a4e287'='xbox'
  '402674cd-4af6-4886-b6ec-0e695bfa0688'='amazon'
}

function Names($items) { if ($items) { @($items | ForEach-Object { $_.Name }) } else { @() } }

if ($Export) {
  $out = foreach ($g in $PlayniteApi.Database.Games) {
    $src = $PLUGINS[$g.PluginId.ToString().ToLower()]; if (-not $src) { $src = 'playnite' }
    [ordered]@{
      name=$g.Name; source=$src; source_id=$g.GameId
      platforms=Names $g.Platforms; genres=Names $g.Genres; tags=Names $g.Tags
      features=Names $g.Features; categories=Names $g.Categories
      developers=Names $g.Developers; publishers=Names $g.Publishers
      series=Names $g.Series; age_ratings=Names $g.AgeRatings; regions=Names $g.Regions
      release_date=$(if($g.ReleaseDate){$g.ReleaseDate.Date.ToString('yyyy-MM-dd')}else{$null})
      release_year=$g.ReleaseYear; playtime=$g.Playtime; play_count=$g.PlayCount
      completion_status=$(if($g.CompletionStatus){$g.CompletionStatus.Name}else{$null})
      user_score=$g.UserScore; critic_score=$g.CriticScore; community_score=$g.CommunityScore
      favorite=$g.Favorite; hidden=$g.Hidden; version=$g.Version
      description=$g.Description; notes=$g.Notes; install_dir=$g.InstallDirectory
      is_installed=$g.IsInstalled; install_size=$g.InstallSize
      links=$(if($g.Links){@($g.Links|ForEach-Object{@{name=$_.Name;url=$_.Url}})}else{@()})
      roms=$(if($g.Roms){@($g.Roms|ForEach-Object{@{name=$_.Name;path=$_.Path}})}else{@()})
      added=$(if($g.Added){$g.Added.ToString('o')}else{$null})
      last_activity=$(if($g.LastActivity){$g.LastActivity.ToString('o')}else{$null})
    }
  }
  $out | ConvertTo-Json -Depth 6 | Out-File -Encoding utf8 $Path
  $PlayniteApi.Dialogs.ShowMessage("Exported $($out.Count) games to $Path")
  return
}

if ($Import) {
  # get-or-create a reference item in a Playnite DB collection by Name
  function Ref($coll, $name) {
    if (-not $name) { return $null }
    $e = $coll | Where-Object { $_.Name -eq $name } | Select-Object -First 1
    if (-not $e) { $e = $coll.Add($name) }
    $e.Id
  }
  function RefList($coll, $names) { @($names | ForEach-Object { Ref $coll $_ }) }

  $recs = Get-Content -Raw -Encoding utf8 $Path | ConvertFrom-Json
  $byName = @{}; foreach ($g in $PlayniteApi.Database.Games) { $byName[$g.Name.ToLower()] = $g }
  $created = 0; $updated = 0
  foreach ($r in $recs) {
    $g = $byName[("" + $r.name).ToLower()]
    $new = $false
    if (-not $g) {
      if ($NoCreate) { continue }
      $g = New-Object "Playnite.SDK.Models.Game"; $g.Name = $r.name
      if ($r.source_id) { $g.GameId = $r.source_id }
      $new = $true
    }
    if ($r.genres)     { $g.GenreIds     = RefList $PlayniteApi.Database.Genres   $r.genres }
    if ($r.tags)       { $g.TagIds       = RefList $PlayniteApi.Database.Tags     $r.tags }
    if ($r.features)   { $g.FeatureIds   = RefList $PlayniteApi.Database.Features  $r.features }
    if ($r.categories) { $g.CategoryIds  = RefList $PlayniteApi.Database.Categories $r.categories }
    if ($r.developers) { $g.DeveloperIds = RefList $PlayniteApi.Database.Companies $r.developers }
    if ($r.publishers) { $g.PublisherIds = RefList $PlayniteApi.Database.Companies $r.publishers }
    if ($r.series)     { $g.SeriesIds    = RefList $PlayniteApi.Database.Series    $r.series }
    if ($r.regions)    { $g.RegionIds    = RefList $PlayniteApi.Database.Regions   $r.regions }
    if ($r.platforms)  { $g.PlatformIds  = RefList $PlayniteApi.Database.Platforms $r.platforms }
    if ($r.release_year) { $g.ReleaseDate = New-Object "Playnite.SDK.Models.ReleaseDate" $r.release_year,1,1 }
    if ($r.description) { $g.Description = $r.description }
    if ($r.user_score)      { $g.UserScore = [int]$r.user_score }
    if ($r.critic_score)    { $g.CriticScore = [int]$r.critic_score }
    if ($r.community_score) { $g.CommunityScore = [int]$r.community_score }
    if ($null -ne $r.favorite) { $g.Favorite = [bool]$r.favorite }
    if ($r.links) { $g.Links = New-Object "System.Collections.ObjectModel.ObservableCollection[Playnite.SDK.Models.Link]"; foreach($l in $r.links){ $g.Links.Add((New-Object "Playnite.SDK.Models.Link" $l.name,$l.url)) } }
    if ($new) { $PlayniteApi.Database.Games.Add($g); $created++ } else { $PlayniteApi.Database.Games.Update($g); $updated++ }
  }
  $PlayniteApi.Dialogs.ShowMessage("Import done: $created created, $updated enriched")
  return
}

Write-Host "Specify -Export or -Import (with -Path)."
