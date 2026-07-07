using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

class Launcher
{
    static int Main()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string app = Path.Combine(root, "app.py");
        if (!File.Exists(app))
        {
            MessageBox.Show("app.py 파일을 찾을 수 없습니다.", "DART-OT");
            return 1;
        }

        string python = FindPython(root);
        if (python == null)
        {
            MessageBox.Show("Python 실행 환경을 찾을 수 없습니다. _start.bat 또는 Python 설치 상태를 확인해 주세요.", "DART-OT");
            return 1;
        }

        ProcessStartInfo info = new ProcessStartInfo();
        info.FileName = python;
        info.Arguments = "\"" + app + "\"";
        info.WorkingDirectory = root;
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        Process.Start(info);
        return 0;
    }

    static string FindPython(string root)
    {
        string[] candidates = new string[]
        {
            Path.Combine(root, "python", "python.exe"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".cache", "codex-runtimes", "codex-primary-runtime", "dependencies", "python", "python.exe"),
            "python.exe",
            "py.exe"
        };

        foreach (string candidate in candidates)
        {
            if (candidate.EndsWith(".exe") && File.Exists(candidate))
            {
                return candidate;
            }
            try
            {
                ProcessStartInfo info = new ProcessStartInfo();
                info.FileName = candidate;
                info.Arguments = "--version";
                info.UseShellExecute = false;
                info.CreateNoWindow = true;
                Process process = Process.Start(info);
                process.WaitForExit(2000);
                if (process.ExitCode == 0)
                {
                    return candidate;
                }
            }
            catch
            {
            }
        }
        return null;
    }
}
