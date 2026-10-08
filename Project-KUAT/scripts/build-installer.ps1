param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path,
    [string]$OutputDirectory = '',
    [string]$DownloadUrl = 'https://shiroha-rin.world/kuat-api/update/KUAT-Setup.exe'
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $ProjectRoot 'release/Installer'
}

$packagedApp = Join-Path $ProjectRoot 'release/Current/KUAT-win32-x64'
if (-not (Test-Path (Join-Path $packagedApp 'KUAT.exe'))) {
    throw "找不到已构建的应用目录：$packagedApp。请先运行 npm run package。"
}

$csc = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
if (-not (Test-Path $csc)) {
    throw "找不到 Windows C# 编译器：$csc"
}

$compression = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/System.IO.Compression.dll'
$compressionFileSystem = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/System.IO.Compression.FileSystem.dll'
$forms = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/System.Windows.Forms.dll'

$work = Join-Path $env:TEMP ('kuat-installer-' + [guid]::NewGuid().ToString('N'))
$stage = Join-Path $work 'stage'
$stubSource = Join-Path $work 'KuatSetup.cs'
$stubExe = Join-Path $work 'KuatSetup.exe'
$payloadZip = Join-Path $work 'KUAT-payload.zip'
$installer = Join-Path $OutputDirectory 'KUAT-Setup.exe'
$portableZip = Join-Path $OutputDirectory 'KUAT-Portable.zip'
$manifestFile = Join-Path $OutputDirectory 'update-manifest.json'

New-Item -ItemType Directory -Path $work, $stage, $OutputDirectory -Force | Out-Null

try {
    # 安装器本体保持很小，真正的 Electron 程序压缩后附加在它的末尾。
    @'
using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

internal static class KuatSetup
{
    private const string Magic = "KUATSET1";
    private const int FooterSize = 16;

    [STAThread]
    private static int Main()
    {
        try
        {
            foreach (var process in Process.GetProcessesByName("KUAT"))
            {
                try
                {
                    if (!process.HasExited)
                    {
                        MessageBox.Show("请先关闭正在运行的 KUAT，再重新运行安装包。", "K.U.A.T 安装", MessageBoxButtons.OK, MessageBoxIcon.Information);
                        return 2;
                    }
                }
                finally
                {
                    process.Dispose();
                }
            }

            var extractionDirectory = Path.Combine(Path.GetTempPath(), "KUAT-Setup-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(extractionDirectory);
            try
            {
                ExtractPayload(extractionDirectory);
                var targetDirectory = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "Programs", "Project-KUAT");
                CopyDirectory(extractionDirectory, targetDirectory);
                CreateShortcut(targetDirectory);
                Process.Start(new ProcessStartInfo
                {
                    FileName = Path.Combine(targetDirectory, "KUAT.exe"),
                    WorkingDirectory = targetDirectory,
                    UseShellExecute = true
                });
                MessageBox.Show("KUAT 已安装到当前用户的本地程序目录。", "K.U.A.T 安装完成", MessageBoxButtons.OK, MessageBoxIcon.Information);
                return 0;
            }
            finally
            {
                try { Directory.Delete(extractionDirectory, true); } catch { }
            }
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "K.U.A.T 安装失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    private static void ExtractPayload(string target)
    {
        using (var file = new FileStream(Assembly.GetEntryAssembly().Location, FileMode.Open, FileAccess.Read, FileShare.Read))
        using (var reader = new BinaryReader(file, Encoding.ASCII, true))
        {
            if (file.Length < FooterSize) throw new InvalidDataException("安装包不完整。");
            file.Seek(-FooterSize, SeekOrigin.End);
            var magic = Encoding.ASCII.GetString(reader.ReadBytes(8));
            var payloadLength = reader.ReadInt64();
            if (magic != Magic || payloadLength < 1 || payloadLength > file.Length - FooterSize)
                throw new InvalidDataException("安装包数据校验失败。");
            file.Seek(file.Length - FooterSize - payloadLength, SeekOrigin.Begin);
            using (var payload = new BoundedStream(file, payloadLength))
            using (var archive = new ZipArchive(payload, ZipArchiveMode.Read, false, Encoding.UTF8))
            {
                foreach (var entry in archive.Entries)
                {
                    var destination = Path.GetFullPath(Path.Combine(target, entry.FullName));
                    if (!destination.StartsWith(Path.GetFullPath(target) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                        throw new InvalidDataException("安装包包含无效路径。");
                    if (string.IsNullOrEmpty(entry.Name))
                    {
                        Directory.CreateDirectory(destination);
                        continue;
                    }
                    Directory.CreateDirectory(Path.GetDirectoryName(destination));
                    using (var input = entry.Open())
                    using (var output = File.Create(destination)) input.CopyTo(output);
                }
            }
        }
    }

    private static void CopyDirectory(string source, string target)
    {
        Directory.CreateDirectory(target);
        foreach (var directory in Directory.GetDirectories(source, "*", SearchOption.AllDirectories))
            Directory.CreateDirectory(Path.Combine(target, directory.Substring(source.Length + 1)));
        foreach (var file in Directory.GetFiles(source, "*", SearchOption.AllDirectories))
        {
            var relative = file.Substring(source.Length + 1);
            var destination = Path.Combine(target, relative);
            Directory.CreateDirectory(Path.GetDirectoryName(destination));
            File.Copy(file, destination, true);
        }
    }

    private static void CreateShortcut(string target)
    {
        var shellType = Type.GetTypeFromProgID("WScript.Shell");
        if (shellType == null) return;
        dynamic shell = Activator.CreateInstance(shellType);
        var executable = Path.Combine(target, "KUAT.exe");
        var desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
        var startMenu = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.StartMenu), "Programs");
        Directory.CreateDirectory(startMenu);
        SaveShortcut(shell, Path.Combine(desktop, "K.U.A.T.lnk"), executable, target);
        SaveShortcut(shell, Path.Combine(startMenu, "K.U.A.T.lnk"), executable, target);
    }

    private static void SaveShortcut(dynamic shell, string path, string executable, string workingDirectory)
    {
        dynamic shortcut = shell.CreateShortcut(path);
        shortcut.TargetPath = executable;
        shortcut.WorkingDirectory = workingDirectory;
        shortcut.IconLocation = executable + ",0";
        shortcut.Description = "K.U.A.T · Project World 创作工作台";
        shortcut.Save();
    }

    private sealed class BoundedStream : Stream
    {
        private readonly Stream inner;
        private long remaining;
        public BoundedStream(Stream inner, long length) { this.inner = inner; remaining = length; }
        public override bool CanRead { get { return true; } }
        public override bool CanSeek { get { return false; } }
        public override bool CanWrite { get { return false; } }
        public override long Length { get { return remaining; } }
        public override long Position { get { return 0; } set { throw new NotSupportedException(); } }
        public override int Read(byte[] buffer, int offset, int count)
        {
            if (remaining <= 0) return 0;
            var read = inner.Read(buffer, offset, (int)Math.Min(count, remaining));
            remaining -= read;
            return read;
        }
        public override void Flush() { }
        public override long Seek(long offset, SeekOrigin origin) { throw new NotSupportedException(); }
        public override void SetLength(long value) { throw new NotSupportedException(); }
        public override void Write(byte[] buffer, int offset, int count) { throw new NotSupportedException(); }
    }
}
'@ | Set-Content -Path $stubSource -Encoding UTF8

    & $csc /nologo /target:winexe /optimize+ /out:$stubExe $stubSource /reference:$compression /reference:$compressionFileSystem /reference:$forms /reference:Microsoft.CSharp.dll
    if ($LASTEXITCODE -ne 0) { throw '安装器引导程序编译失败。' }

    # 使用系统压缩库生成便携包，避免额外下载或安装打包工具。
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory($packagedApp, $payloadZip, [System.IO.Compression.CompressionLevel]::Fastest, $false)
    Copy-Item $payloadZip $portableZip -Force

    $stubBytes = [System.IO.File]::ReadAllBytes($stubExe)
    $payloadBytes = [System.IO.File]::ReadAllBytes($payloadZip)
    $magicBytes = [System.Text.Encoding]::ASCII.GetBytes('KUATSET1')
    $lengthBytes = [System.BitConverter]::GetBytes([int64]$payloadBytes.Length)
    $output = [System.IO.File]::Open($installer, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    try {
        $output.Write($stubBytes, 0, $stubBytes.Length)
        $output.Write($payloadBytes, 0, $payloadBytes.Length)
        $output.Write($magicBytes, 0, $magicBytes.Length)
        $output.Write($lengthBytes, 0, $lengthBytes.Length)
    }
    finally { $output.Dispose() }

    # 兼容系统自带的旧版 Windows PowerShell，不依赖 Get-FileHash。
    $sha256 = New-Object System.Security.Cryptography.SHA256Managed
    $hash = ([BitConverter]::ToString($sha256.ComputeHash([System.IO.File]::ReadAllBytes($installer)))).Replace('-', '')
    # Windows PowerShell 5.1 按系统代码页读取无 BOM 的 package.json；版本号用 ASCII 正则读取，避免中文描述影响清单生成。
    $packageText = Get-Content (Join-Path $ProjectRoot 'package.json') -Raw
    $version = [regex]::Match($packageText, '"version"\s*:\s*"([^"]+)"').Groups[1].Value
    if ([string]::IsNullOrWhiteSpace($version)) { throw '无法从 package.json 读取版本号。' }
    [ordered]@{
        version = $version
        notes = '新增世界模拟决策链：支持角色档案、事件目标、有限干预、逐轮决策草稿与历史追踪。'
        downloadUrl = $DownloadUrl
        sha256 = $hash.ToLowerInvariant()
        sizeBytes = (Get-Item $installer).Length
    } | ConvertTo-Json | Set-Content -Path $manifestFile -Encoding UTF8
    [pscustomobject]@{
        Installer = (Resolve-Path $installer).Path
        Portable  = (Resolve-Path $portableZip).Path
        Manifest  = (Resolve-Path $manifestFile).Path
        SizeBytes = (Get-Item $installer).Length
        SHA256    = $hash
    } | Format-List
}
finally {
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
}
