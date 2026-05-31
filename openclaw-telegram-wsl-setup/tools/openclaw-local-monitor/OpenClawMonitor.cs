using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Globalization;
using System.IO;
using System.IO.Pipes;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using System.Runtime.InteropServices;

namespace OpenClawLocalMonitor
{
    static class Program
    {
        internal const string InstanceMutexName = "Local\\OpenClawControlMonitor";
        internal const string InstancePipeName = "OpenClawControlMonitor.Show";

        [STAThread]
        static void Main()
        {
            bool createdNew;
            using (var mutex = new System.Threading.Mutex(true, InstanceMutexName, out createdNew))
            {
                if (!createdNew)
                {
                    SignalExistingInstance();
                    return;
                }

                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new MonitorForm());
            }
        }

        static void SignalExistingInstance()
        {
            try
            {
                using (var pipe = new NamedPipeClientStream(".", InstancePipeName, PipeDirection.Out))
                using (var writer = new StreamWriter(pipe, Encoding.UTF8))
                {
                    pipe.Connect(800);
                    writer.WriteLine("show");
                    writer.Flush();
                }
            }
            catch
            {
            }
        }
    }

    sealed class CommandResult
    {
        public bool Ok;
        public int ExitCode;
        public string Stdout = "";
        public string Stderr = "";
        public string Error = "";
    }

    enum ClosePreference
    {
        Ask,
        MinimizeToTray,
        Exit
    }

    sealed class CloseChoice
    {
        public bool Cancelled = true;
        public ClosePreference Preference = ClosePreference.MinimizeToTray;
        public bool Remember;
    }

    sealed class CloseChoiceDialog : Form
    {
        readonly CheckBox remember;
        public CloseChoice Choice = new CloseChoice();

        public CloseChoiceDialog(Icon icon)
        {
            Text = "关闭 OpenClaw 控制中心";
            StartPosition = FormStartPosition.CenterParent;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            ClientSize = new Size(420, 190);
            BackColor = Color.White;
            ForeColor = Color.FromArgb(31, 41, 55);
            Font = new Font("Microsoft YaHei UI", 9f);
            if (icon != null) Icon = icon;

            var title = new Label
            {
                Text = "关闭控制中心？",
                Left = 22,
                Top = 18,
                Width = 360,
                Height = 26,
                Font = new Font(Font.FontFamily, 12f, FontStyle.Bold)
            };
            var body = new Label
            {
                Text = "可以让它留在系统托盘继续监控，也可以直接退出。",
                Left = 22,
                Top = 52,
                Width = 370,
                Height = 48
            };
            remember = new CheckBox
            {
                Text = "记住我的选择",
                Left = 22,
                Top = 106,
                Width = 220,
                Height = 24
            };

            var trayButton = MakeDialogButton("最小化到托盘", 22, 144, 130);
            trayButton.Click += (s, e) => Choose(ClosePreference.MinimizeToTray);
            var exitButton = MakeDialogButton("关闭程序", 160, 144, 110);
            exitButton.Click += (s, e) => Choose(ClosePreference.Exit);
            var cancelButton = MakeDialogButton("取消", 282, 144, 90);
            cancelButton.Click += (s, e) => { Choice.Cancelled = true; DialogResult = DialogResult.Cancel; Close(); };

            Controls.AddRange(new Control[] { title, body, remember, trayButton, exitButton, cancelButton });
            AcceptButton = trayButton;
            CancelButton = cancelButton;
        }

        static Button MakeDialogButton(string text, int left, int top, int width)
        {
            return new Button
            {
                Text = text,
                Left = left,
                Top = top,
                Width = width,
                Height = 32,
                FlatStyle = FlatStyle.System
            };
        }

        void Choose(ClosePreference preference)
        {
            Choice.Cancelled = false;
            Choice.Preference = preference;
            Choice.Remember = remember.Checked;
            DialogResult = DialogResult.OK;
            Close();
        }
    }

    sealed class Snapshot
    {
        public DateTime GeneratedAt = DateTime.Now;
        public string State = "Idle";
        public bool GatewayOk;
        public bool OpenClawServiceActive;
        public bool GatewaySoftFailure;
        public bool TelegramOk;
        public string TelegramCardState = "warn";
        public long TelegramLastStartAt = -1;
        public long TelegramLastInboundAt = -1;
        public long TelegramLastOutboundAt = -1;
        public int StartupProgress;
        public string StartupStep = "等待检测";
        public string StartupProgressText = "等待首次刷新。";
        public int RunningTasks;
        public int AuditWarnings;
        public int AuditErrors;
        public string GatewayText = "-";
        public string TelegramText = "-";
        public string RecentSessionAge = "-";
        public string StatusLine = "";
        public string Error = "";
        public string ReliabilityStatus = "";
        public string ReliabilitySummaryText = "";
        public bool ExternalNetworkIssue;
        public long TokenTotal;
        public long TokenInput;
        public long TokenOutput;
        public long TokenCacheRead;
        public string TokenContext = "-";
        public string CostText = "-";
        public string CostState = "warn";
        public bool UsageCacheVisible;
        public bool UsageCacheStale;
        public string UsageCacheAge = "-";
        public long LastSessionAgeMs = -1;
        public string LastSessionSource = "-";
        public string LastSessionModel = "-";
        public int FlowActive;
        public int FlowBlocked;
        public int FlowCancelRequested;
        public int LocalWorkItems;
        public bool LocalDaemonActive;
        public string LocalWorkAge = "-";
        public readonly List<string[]> Tasks = new List<string[]>();
        public readonly List<string> Sessions = new List<string>();
        public string CollabStatus = "";
        public readonly List<string> Logs = new List<string>();
        public readonly List<string> TokenFlows = new List<string>();
    }

    sealed class LocalGatewayFacts
    {
        public bool ServiceActive;
        public bool ProcessRunning;
        public bool PortListening;
        public string ServiceState = "";
        public string Pid = "";
        public string CpuPercent = "";
        public string RssMb = "";
        public string Uptime = "";
        public string Error = "";
    }

    sealed class TelegramLocalSignal
    {
        public long LastOkAgeMs = -1;
        public long LastFailureAgeMs = -1;
        public string LastOkLine = "";
        public string LastFailureLine = "";
        public string Error = "";
    }

    sealed class DiagnosticsSnapshot
    {
        public DateTime GeneratedAt = DateTime.Now;
        public string OverallState = "Unknown";
        public readonly List<string> OverallReasons = new List<string>();
        public readonly List<DiagnosticItem> Gateway = new List<DiagnosticItem>();
        public readonly List<DiagnosticItem> GatewayResilience = new List<DiagnosticItem>();
        public readonly List<DiagnosticItem> NetworkStability = new List<DiagnosticItem>();
        public readonly List<DiagnosticItem> EntrancePressure = new List<DiagnosticItem>();
        public readonly List<DiagnosticItem> Telegram = new List<DiagnosticItem>();
        public readonly List<DiagnosticItem> Sessions = new List<DiagnosticItem>();
        public readonly List<DiagnosticItem> TasksLogs = new List<DiagnosticItem>();
    }

    sealed class DiagnosticItem
    {
        public string Label;
        public string Value;
        public string State;
        public string Reason;
        public string Source;

        public DiagnosticItem(string label, string value, string state, string reason, string source)
        {
            Label = label ?? "-";
            Value = value ?? "-";
            State = state ?? "Unknown";
            Reason = reason ?? "";
            Source = source ?? "";
        }
    }

    sealed class CostSummary
    {
        public DateTime UpdatedAt = DateTime.MinValue;
        public bool Available;
        public double TotalCost;
        public string Error = "";
        public readonly List<string> Lines = new List<string>();
    }

    sealed class UsageCacheSummary
    {
        public bool Available;
        public bool Stale;
        public string Status = "";
        public string Error = "";
        public string GeneratedAt = "";
        public long AgeMs = -1;
        public long InputTokens;
        public long OutputTokens;
        public long TotalTokens;
        public long CacheReadTokens;
        public long CacheWriteTokens;
        public double EstimatedCost;
        public bool HasEstimatedCost;
        public string CostPeriod = "";
        public string SessionKey = "";
        public long SessionTotalTokens;
        public long SessionContextTokens;
        public long SessionContextLimit;
        public readonly List<string> Lines = new List<string>();
    }

    sealed class ReliabilitySummary
    {
        public bool Available;
        public bool Stale;
        public string Status = "";
        public string Error = "";
        public string GeneratedAt = "";
        public long AgeMs = -1;
        public string Summary = "";
        public readonly List<string> Kinds = new List<string>();
        public readonly List<string> Lines = new List<string>();
    }

    sealed class MonitorForm : Form
    {
        const string WslDistro = "Ubuntu";
        const string OpenClawAbsolutePath = "/home/lcxhh/.local/bin/openclaw";
        static readonly bool MainPanelAutoRefreshEnabled = false;
        const int AutoRefreshIntervalMs = 120000;
        readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = int.MaxValue, RecursionLimit = 100 };
        readonly Timer timer = new Timer();
        readonly Timer clashTimer = new Timer();
        readonly object costLock = new object();
        readonly object artifactLock = new object();
        readonly long monitorStartedAtMs = (long)(DateTime.UtcNow - new DateTime(1970, 1, 1)).TotalMilliseconds;
        readonly long activeTaskEventWindowMs = 20L * 60L * 1000L;
        readonly long freshTaskEventWindowMs = 2L * 60L * 1000L;
        Dictionary<string, long> previousArtifactMtimes = new Dictionary<string, long>();
        CostSummary cachedCost = new CostSummary();
        bool tokenSectionVisible;
        bool artifactBaselineReady;
        bool refreshing;
        bool enforcingClashMode;
        int gatewayProbeFailures;
        ClosePreference closePreference = ClosePreference.Ask;
        bool clashSafeModeEnabled = true;
        ToolStripMenuItem clashSafeModeTrayItem;

        Label headerTitle;
        Label updated;
        Label statusLine;
        Label tokenHeader;
        Label taskHeader;
        Label sessionHeader;
        Label logHeader;
        Button diagnosticsButton;
        Button openClawPowerButton;
        CheckBox clashSafeModeCheck;
        RoundedPanel hoverTip;
        Label hoverTipText;
        Card overall;
        Card gateway;
        Card telegram;
        Card tasks;
        Card audit;
        Card session;
        Card tokenTotal;
        Card tokenInput;
        Card tokenOutput;
        Card tokenCache;
        Card tokenCost;
        RoundedPanel costHintPopup;
        Label heroTitle;
        Label heroDetail;
        RoundedPanel startupProgressPanel;
        Label startupProgressText;
        ProgressBar startupProgressBar;
        Label legendLine;
        DataGridView taskGrid;
        ListBox sessionList;
        ListBox logList;
        Label collabStatusLabel;
        Button openControlButton;
        NotifyIcon trayIcon;
        ContextMenuStrip trayMenu;
        NamedPipeServerStream restorePipe;
        bool allowExit;
        bool trayNoticeShown;
        bool togglingOpenClaw;
        bool lastGatewayOk;
        bool lastOpenClawServiceActive;
        string lastDiagnosticsGatewayPid = "";
        bool wasMinimized;
        bool smoothRestorePending;
        string startupNote = "";
        ToolStripMenuItem openClawPowerTrayItem;

        public MonitorForm()
        {
            Text = "OpenClaw 控制中心";
            StartPosition = FormStartPosition.CenterScreen;
            MinimumSize = new Size(1000, 760);
            ClientSize = new Size(1220, 900);
            AutoScroll = true;
            DoubleBuffered = true;
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
            UpdateStyles();
            BackColor = Color.FromArgb(246, 248, 252);
            ForeColor = Color.FromArgb(31, 41, 55);
            Font = new Font("Microsoft YaHei UI", 9f);
            var iconPath = Path.Combine(Application.StartupPath, "OpenClawMonitor.ico");
            if (File.Exists(iconPath)) Icon = new Icon(iconPath);

            clashSafeModeEnabled = LoadClashSafeModeEnabled();
            BuildUi();
            Resize += (s, e) => OnMonitorResize();
            SetupTray();
            closePreference = LoadClosePreference();
            if (MainPanelAutoRefreshEnabled)
            {
                timer.Interval = AutoRefreshIntervalMs;
                timer.Tick += async (s, e) =>
                {
                    if (!Visible || WindowState == FormWindowState.Minimized) return;
                    await RefreshAsync();
                };
                timer.Start();
            }
            clashTimer.Interval = 2500;
            clashTimer.Tick += async (s, e) => await EnsureClashSafeModeAsync(false);
            clashTimer.Start();
            Shown += async (s, e) =>
            {
                LayoutUi();
                Invalidate(true);
                Update();
                await EnsureClashSafeModeAsync(true);
                await RefreshAsync();
            };
            FormClosing += OnFormClosing;
            StartRestorePipe();
        }

        protected override void WndProc(ref Message m)
        {
            const int WM_SYSCOMMAND = 0x0112;
            const int SC_RESTORE = 0xF120;

            if (m.Msg == WM_SYSCOMMAND && ((int)m.WParam & 0xFFF0) == SC_RESTORE)
                BeginSmoothRestore();

            base.WndProc(ref m);

            if (m.Msg == WM_SYSCOMMAND && ((int)m.WParam & 0xFFF0) == SC_RESTORE)
                FinishSmoothRestore(true);
        }

        void OnFormClosing(object sender, FormClosingEventArgs e)
        {
            if (allowExit)
            {
                if (trayIcon != null) trayIcon.Visible = false;
                return;
            }

            var preference = closePreference;
            if (preference == ClosePreference.Ask)
            {
                using (var dialog = new CloseChoiceDialog(Icon))
                {
                    dialog.ShowDialog(this);
                    if (dialog.Choice.Cancelled)
                    {
                        e.Cancel = true;
                        return;
                    }
                    preference = dialog.Choice.Preference;
                    if (dialog.Choice.Remember)
                    {
                        closePreference = preference;
                        SaveClosePreference(preference);
                    }
                }
            }

            if (preference == ClosePreference.MinimizeToTray)
            {
                e.Cancel = true;
                HideToTray();
                return;
            }

            allowExit = true;
            if (trayIcon != null) trayIcon.Visible = false;
        }

        ClosePreference LoadClosePreference()
        {
            try
            {
                var path = SettingsPath();
                if (!File.Exists(path)) return ClosePreference.Ask;
                var data = json.Deserialize<Dictionary<string, object>>(File.ReadAllText(path, Encoding.UTF8));
                object value;
                if (!data.TryGetValue("closePreference", out value)) return ClosePreference.Ask;
                var text = Convert.ToString(value);
                if (text == "tray") return ClosePreference.MinimizeToTray;
                if (text == "exit") return ClosePreference.Exit;
            }
            catch
            {
            }
            return ClosePreference.Ask;
        }

        void SaveClosePreference(ClosePreference preference)
        {
            try
            {
                var path = SettingsPath();
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                var text = preference == ClosePreference.MinimizeToTray ? "tray" : preference == ClosePreference.Exit ? "exit" : "ask";
                var data = LoadSettings();
                data["closePreference"] = text;
                SaveSettings(data);
            }
            catch
            {
            }
        }

        bool LoadClashSafeModeEnabled()
        {
            try
            {
                var data = LoadSettings();
                object value;
                if (!data.TryGetValue("clashSafeMode", out value)) return true;
                return Convert.ToString(value) != "off";
            }
            catch
            {
            }
            return true;
        }

        void SaveClashSafeModeEnabled(bool enabled)
        {
            try
            {
                var data = LoadSettings();
                data["clashSafeMode"] = enabled ? "on" : "off";
                SaveSettings(data);
            }
            catch
            {
            }
        }

        Dictionary<string, object> LoadSettings()
        {
            try
            {
                var path = SettingsPath();
                if (!File.Exists(path)) return new Dictionary<string, object>();
                var data = json.Deserialize<Dictionary<string, object>>(File.ReadAllText(path, Encoding.UTF8));
                return data ?? new Dictionary<string, object>();
            }
            catch
            {
                return new Dictionary<string, object>();
            }
        }

        void SaveSettings(Dictionary<string, object> data)
        {
            var path = SettingsPath();
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            File.WriteAllText(path, json.Serialize(data), Encoding.UTF8);
        }

        static string SettingsPath()
        {
            return Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                "OpenClawMonitor",
                "settings.json");
        }

        void SetupTray()
        {
            trayMenu = new ContextMenuStrip();
            trayMenu.Items.Add("显示面板", null, (s, e) => ShowFromTray());
            openClawPowerTrayItem = new ToolStripMenuItem("开启 OpenClaw", null, async (s, e) =>
            {
                ShowFromTray();
                await ToggleOpenClawAsync();
            });
            trayMenu.Items.Add(openClawPowerTrayItem);
            trayMenu.Items.Add("打开 Control", null, (s, e) => OpenControl());
            clashSafeModeTrayItem = new ToolStripMenuItem("Clash 安全模式", null, async (s, e) => await ToggleClashSafeModeAsync()) { Checked = clashSafeModeEnabled };
            trayMenu.Items.Add(clashSafeModeTrayItem);
            trayMenu.Items.Add("诊断", null, async (s, e) =>
            {
                ShowFromTray();
                await RefreshDiagnosticsAsync();
            });
            trayMenu.Items.Add(new ToolStripSeparator());
            trayMenu.Items.Add("退出控制中心", null, (s, e) =>
            {
                allowExit = true;
                trayIcon.Visible = false;
                Close();
            });

            trayIcon = new NotifyIcon
            {
                Text = "OpenClaw 控制中心",
                Icon = Icon,
                Visible = true,
                ContextMenuStrip = trayMenu
            };
            trayIcon.DoubleClick += (s, e) => ShowFromTray();
        }

        void StartRestorePipe()
        {
            Task.Run(() =>
            {
                while (!IsDisposed)
                {
                    try
                    {
                        using (var pipe = new NamedPipeServerStream(Program.InstancePipeName, PipeDirection.In, 1, PipeTransmissionMode.Message, PipeOptions.Asynchronous))
                        {
                            restorePipe = pipe;
                            pipe.WaitForConnection();
                            if (!IsDisposed)
                                BeginInvoke(new Action(RestoreToForeground));
                        }
                    }
                    catch
                    {
                        if (IsDisposed) return;
                        System.Threading.Thread.Sleep(300);
                    }
                }
            });
        }

        void HideToTray()
        {
            Hide();
            ShowInTaskbar = false;
            if (!trayNoticeShown)
            {
                trayNoticeShown = true;
                trayIcon.ShowBalloonTip(1800, "OpenClaw 控制中心", "已在后台托盘运行。双击图标可打开。", ToolTipIcon.Info);
            }
        }

        void ShowFromTray()
        {
            RestoreToForeground();
        }

        void RestoreToForeground()
        {
            if (InvokeRequired)
            {
                BeginInvoke(new Action(RestoreToForeground));
                return;
            }

            smoothRestorePending = false;
            Opacity = 1;
            ShowInTaskbar = true;
            if (!Visible) Show();
            if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
            EnsureWindowOnScreen();
            TopMost = true;
            BringToFront();
            Activate();
            Focus();
            TopMost = false;
        }

        void EnsureWindowOnScreen()
        {
            var current = Screen.FromControl(this).WorkingArea;
            var visibleEnough = Bounds.IntersectsWith(current);
            if (visibleEnough) return;

            var area = Screen.PrimaryScreen.WorkingArea;
            Width = Math.Min(Math.Max(Width, MinimumSize.Width), area.Width);
            Height = Math.Min(Math.Max(Height, MinimumSize.Height), area.Height);
            Left = area.Left + Math.Max(0, (area.Width - Width) / 2);
            Top = area.Top + Math.Max(0, (area.Height - Height) / 2);
        }

        void OnMonitorResize()
        {
            if (WindowState == FormWindowState.Minimized)
            {
                wasMinimized = true;
                return;
            }

            LayoutUi();
            if (wasMinimized)
            {
                BeginSmoothRestore();
                FinishSmoothRestore(false);
                wasMinimized = false;
            }
        }

        void BeginSmoothRestore()
        {
            if (smoothRestorePending) return;
            smoothRestorePending = true;
            Opacity = 1;
        }

        void FinishSmoothRestore(bool activate)
        {
            BeginInvoke(new Action(() =>
            {
                LayoutUi();
                Invalidate(true);
                Update();
                Opacity = 1;
                smoothRestorePending = false;
                if (activate) RestoreToForeground();
            }));
        }

        void BuildUi()
        {
            headerTitle = MakeLabel("OpenClaw 控制中心", 28, 20, 360, 34, 20f, Color.FromArgb(15, 23, 42), true);
            Controls.Add(headerTitle);

            updated = MakeLabel("", 840, 28, 230, 24, 9f, Color.FromArgb(100, 116, 139), false);
            Controls.Add(updated);
            openClawPowerButton = new Button
            {
                Text = "开启 OpenClaw",
                Location = new Point(816, 20),
                Size = new Size(130, 36),
                BackColor = Color.FromArgb(22, 163, 74),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat
            };
            openClawPowerButton.FlatAppearance.BorderSize = 0;
            openClawPowerButton.Click += async (s, e) => await ToggleOpenClawAsync();
            AddBoundedHoverTip(openClawPowerButton, "手动启动或关闭 OpenClaw gateway。");
            Controls.Add(openClawPowerButton);

            openControlButton = new Button
            {
                Text = "原生 Control",
                Location = new Point(962, 20),
                Size = new Size(112, 36),
                BackColor = Color.FromArgb(15, 23, 42),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat
            };
            openControlButton.FlatAppearance.BorderSize = 0;
            openControlButton.Click += (s, e) => OpenControl();
            AddBoundedHoverTip(openControlButton, "高级入口，可能较重。");
            Controls.Add(openControlButton);

            diagnosticsButton = new Button
            {
                Text = "诊断",
                Location = new Point(1080, 20),
                Size = new Size(72, 36),
                BackColor = Color.FromArgb(99, 102, 241),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat
            };
            diagnosticsButton.FlatAppearance.BorderSize = 0;
            diagnosticsButton.Click += async (s, e) => await RefreshDiagnosticsAsync();
            AddBoundedHoverTip(diagnosticsButton, "只读诊断：不启动、不重启、不改配置。");
            Controls.Add(diagnosticsButton);

            clashSafeModeCheck = new CheckBox
            {
                Text = "Clash 安全模式",
                Location = new Point(398, 27),
                Size = new Size(180, 24),
                Checked = clashSafeModeEnabled,
                BackColor = Color.Transparent,
                ForeColor = Color.FromArgb(31, 41, 55)
            };
            clashSafeModeCheck.CheckedChanged += async (s, e) =>
            {
                if (clashSafeModeEnabled == clashSafeModeCheck.Checked) return;
                clashSafeModeEnabled = clashSafeModeCheck.Checked;
                SaveClashSafeModeEnabled(clashSafeModeEnabled);
                SyncClashSafeModeUi();
                await EnsureClashSafeModeAsync(true);
            };
            AddBoundedHoverTip(clashSafeModeCheck, "用于开全局/TUN 后国内应用或链接受影响的场景；没开全局/TUN 时通常不用开启，开启后 OpenClaw/Codex 跟随 GLOBAL，微信和国内连接按规则直连。");
            Controls.Add(clashSafeModeCheck);

            var hero = new RoundedPanel
            {
                Location = new Point(28, 92),
                Size = new Size(1154, 118),
                BackColor = Color.White,
                BorderColor = Color.FromArgb(226, 232, 240),
                Radius = 18
            };
            heroTitle = MakeLabel("正在检查 OpenClaw...", 28, 18, 520, 38, 22f, Color.FromArgb(15, 23, 42), true);
            heroDetail = MakeLabel("正在等待首次刷新。", 30, 60, 1000, 28, 10f, Color.FromArgb(71, 85, 105), false);
            hero.Controls.AddRange(new Control[] { heroTitle, heroDetail });
            Controls.Add(hero);

            startupProgressPanel = new RoundedPanel
            {
                Location = new Point(30, 88),
                Size = new Size(1094, 22),
                BackColor = Color.White,
                BorderColor = Color.FromArgb(226, 232, 240),
                Radius = 9,
                Visible = false
            };
            startupProgressText = MakeLabel("启动进度", 12, 2, 340, 18, 8.5f, Color.FromArgb(71, 85, 105), false);
            startupProgressBar = new ProgressBar
            {
                Location = new Point(370, 5),
                Size = new Size(700, 12),
                Minimum = 0,
                Maximum = 100,
                Style = ProgressBarStyle.Continuous
            };
            startupProgressPanel.Controls.AddRange(new Control[] { startupProgressText, startupProgressBar });
            hero.Controls.Add(startupProgressPanel);

            overall = new Card("状态", 28, 232, 176, 88);
            gateway = new Card("网关", 220, 232, 176, 88);
            telegram = new Card("Telegram", 412, 232, 176, 88);
            tasks = new Card("后台待处理", 604, 232, 176, 88);
            audit = new Card("提醒", 796, 232, 176, 88);
            session = new Card("最近活动", 988, 232, 194, 88);
            telegram.Panel.Visible = false;
            Controls.AddRange(new Control[] { overall.Panel, gateway.Panel, tasks.Panel, audit.Panel, session.Panel });
            AddCardHoverTip(gateway, "这里显示 gateway 进程 CPU。数值高会变慢，但不等于重启或不可用。");
            AddCardHoverTip(tasks, "来自本地 task.json，统计未结束任务；不调用 gateway。");

            tokenHeader = MakeLabel("Token / 成本流向", 28, 344, 260, 24, 12f, Color.FromArgb(15, 23, 42), true);
            tokenHeader.Visible = false;
            Controls.Add(tokenHeader);
            tokenTotal = new Card("今日 Token", 28, 376, 142, 84);
            tokenInput = new Card("输入 Token", 184, 376, 142, 84);
            tokenOutput = new Card("输出 Token", 340, 376, 142, 84);
            tokenCache = new Card("缓存读取", 496, 376, 142, 84);
            tokenCost = new Card("已记录成本", 652, 376, 128, 84);
            Controls.AddRange(new Control[] { tokenTotal.Panel, tokenInput.Panel, tokenOutput.Panel, tokenCache.Panel, tokenCost.Panel });
            foreach (var card in new[] { tokenTotal, tokenInput, tokenOutput, tokenCache, tokenCost })
                card.Panel.Visible = false;
            AddCostHint();

            taskHeader = MakeLabel("后台活动状态", 28, 486, 260, 24, 12f, Color.FromArgb(15, 23, 42), true);
            taskHeader.Visible = false;
            Controls.Add(taskHeader);
            taskGrid = new SmoothDataGridView
            {
                Location = new Point(28, 516),
                Size = new Size(1154, 150),
                Visible = false,
                BackgroundColor = Color.White,
                GridColor = Color.FromArgb(226, 232, 240),
                ForeColor = Color.FromArgb(31, 41, 55),
                RowHeadersVisible = false,
                AllowUserToAddRows = false,
                AllowUserToDeleteRows = false,
                ReadOnly = true,
                AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.Fill,
                EnableHeadersVisualStyles = false
            };
            taskGrid.DefaultCellStyle.BackColor = Color.White;
            taskGrid.DefaultCellStyle.ForeColor = Color.FromArgb(31, 41, 55);
            taskGrid.DefaultCellStyle.SelectionBackColor = Color.FromArgb(219, 234, 254);
            taskGrid.DefaultCellStyle.SelectionForeColor = Color.FromArgb(30, 64, 175);
            taskGrid.ColumnHeadersDefaultCellStyle.BackColor = Color.FromArgb(241, 245, 249);
            taskGrid.ColumnHeadersDefaultCellStyle.ForeColor = Color.FromArgb(51, 65, 85);
            taskGrid.Columns.Add("label", "任务");
            taskGrid.Columns.Add("runtime", "类型");
            taskGrid.Columns.Add("status", "状态");
            taskGrid.Columns.Add("age", "持续");
            taskGrid.Columns.Add("last", "最近事件");
            Controls.Add(taskGrid);

            Controls.Add(MakeLabel("Agent 活动", 28, 692, 240, 24, 12f, Color.FromArgb(15, 23, 42), true));
            sessionList = MakeList(28, 722, 560, 120);
            Controls.Add(sessionList);

            Controls.Add(MakeLabel("最近提醒", 622, 692, 330, 24, 12f, Color.FromArgb(15, 23, 42), true));
            logList = MakeList(622, 722, 560, 120);
            Controls.Add(logList);

            statusLine = MakeLabel("", 28, 852, 1154, 24, 9f, Color.FromArgb(100, 116, 139), false);
            Controls.Add(statusLine);
            legendLine = MakeLabel("绿色=就绪，蓝色=正在工作，黄色=需要留意，红色=需要处理。", 28, 874, 1154, 22, 8.5f, Color.FromArgb(148, 163, 184), false);
            Controls.Add(legendLine);
            collabStatusLabel = MakeLabel("", 28, 662, 1154, 20, 9f, Color.FromArgb(100, 116, 139), false);
            Controls.Add(collabStatusLabel);
            BuildHoverTip();
            LayoutUi();
        }

        void BuildHoverTip()
        {
            hoverTip = new RoundedPanel
            {
                Size = new Size(180, 34),
                BackColor = Color.White,
                BorderColor = Color.FromArgb(203, 213, 225),
                Radius = 10,
                Visible = false
            };
            hoverTipText = new Label
            {
                Location = new Point(12, 9),
                Size = new Size(156, 18),
                AutoEllipsis = false,
                TextAlign = ContentAlignment.MiddleLeft,
                ForeColor = Color.FromArgb(51, 65, 85),
                Font = new Font("Microsoft YaHei UI", 8.5f),
                BackColor = Color.Transparent
            };
            hoverTip.Controls.Add(hoverTipText);
            Controls.Add(hoverTip);
            hoverTip.BringToFront();
        }

        void AddBoundedHoverTip(Control target, string text)
        {
            target.MouseEnter += (s, e) => ShowBoundedHoverTip(target, text);
            target.MouseLeave += (s, e) => HideBoundedHoverTip();
        }

        void AddCardHoverTip(Card card, string text)
        {
            AddBoundedHoverTip(card.Panel, text);
            foreach (Control child in card.Panel.Controls)
                AddBoundedHoverTip(child, text);
        }

        void ShowBoundedHoverTip(Control target, string text)
        {
            if (hoverTip == null || hoverTipText == null) return;
            hoverTipText.Text = text;
            var maxWidth = Math.Min(380, Math.Max(180, ClientSize.Width - 56));
            var single = TextRenderer.MeasureText(text, hoverTipText.Font, new Size(int.MaxValue, int.MaxValue), TextFormatFlags.NoPadding);
            var bodyWidth = Math.Min(Math.Max(single.Width, 132), maxWidth - 24);
            var measured = TextRenderer.MeasureText(text, hoverTipText.Font, new Size(bodyWidth, int.MaxValue), TextFormatFlags.WordBreak | TextFormatFlags.NoPadding);
            var width = Math.Min(maxWidth, Math.Max(156, measured.Width + 26));
            var height = Math.Max(34, measured.Height + 20);
            var screenPoint = target.PointToScreen(new Point(0, target.Height + 8));
            var local = PointToClient(screenPoint);
            var x = Math.Max(28, Math.Min(local.X, ClientSize.Width - width - 28));
            var y = local.Y;
            if (y + height > ClientSize.Height - 28)
                y = PointToClient(target.PointToScreen(new Point(0, -height - 8))).Y;
            y = Math.Max(28, y);
            hoverTip.SetBounds(x, y, width, height);
            hoverTipText.SetBounds(12, 9, width - 24, height - 18);
            hoverTip.Visible = true;
            hoverTip.BringToFront();
        }

        void HideBoundedHoverTip()
        {
            if (hoverTip != null) hoverTip.Visible = false;
        }

        void LayoutUi()
        {
            if (diagnosticsButton == null || taskGrid == null) return;
            SuspendLayout();
            try
            {
                var compact = ClientSize.Width < 1180;
                var margin = compact ? 18 : 28;
                var gap = compact ? 10 : 16;
                var diagnosticsWidth = compact ? 68 : 72;
                var openControlWidth = compact ? 104 : 112;
                var openClawPowerWidth = compact ? 122 : 130;
                var contentWidth = Math.Max(760, ClientSize.Width - margin * 2);
                var clientHeight = Math.Max(680, ClientSize.Height);

                diagnosticsButton.SetBounds(margin + contentWidth - diagnosticsWidth, 20, diagnosticsWidth, 36);
                openControlButton.SetBounds(diagnosticsButton.Left - gap - openControlWidth, 20, openControlWidth, 36);
                openClawPowerButton.SetBounds(openControlButton.Left - gap - openClawPowerWidth, 20, openClawPowerWidth, 36);
                var clashLeft = margin + (compact ? 330 : 390);
                var clashWidth = compact ? 168 : 180;
                var clashAvailableWidth = openClawPowerButton.Left - gap - clashLeft;
                var clashOnSecondRow = clashAvailableWidth < clashWidth;
                var topExtra = clashOnSecondRow ? 34 : 0;
                headerTitle.SetBounds(margin, 20, Math.Max(260, (clashOnSecondRow ? openClawPowerButton.Left : clashLeft) - margin - gap), 34);
                clashSafeModeCheck.Text = clashOnSecondRow ? "Clash 安全模式" : "Clash 安全模式";
                clashSafeModeCheck.SetBounds(clashOnSecondRow ? margin : clashLeft, clashOnSecondRow ? 62 : 27, clashOnSecondRow ? clashWidth : Math.Min(clashWidth, clashAvailableWidth), 24);

                var updatedRight = openClawPowerButton.Left - gap;
                var updatedDesiredWidth = 230;
                var updatedLeft = updatedRight - updatedDesiredWidth;
                var minimumUpdatedLeft = clashSafeModeCheck.Right + gap;
                if (updatedLeft < minimumUpdatedLeft)
                    updatedLeft = minimumUpdatedLeft;
                var updatedWidth = updatedRight - updatedLeft;
                updated.Visible = !clashOnSecondRow && updatedWidth >= 130;
                if (updated.Visible)
                    updated.SetBounds(updatedLeft, 28, Math.Min(updatedDesiredWidth, updatedWidth), 24);

                var hero = Controls.OfType<RoundedPanel>().FirstOrDefault(p => p.Controls.Contains(heroTitle));
                if (hero != null)
                {
                    hero.SetBounds(margin, 92 + topExtra, contentWidth, 118);
                    heroTitle.SetBounds(28, 18, Math.Max(420, contentWidth - 56), 38);
                    heroDetail.SetBounds(30, 60, Math.Max(420, contentWidth - 60), 26);
                    if (startupProgressPanel != null)
                    {
                        var progressWidth = Math.Max(420, contentWidth - 60);
                        startupProgressPanel.SetBounds(30, 88, progressWidth, 22);
                        startupProgressText.SetBounds(12, 2, Math.Max(180, progressWidth - 420), 18);
                        startupProgressBar.SetBounds(Math.Max(220, progressWidth - 700), 5, Math.Min(680, progressWidth - 240), 12);
                    }
                }

                var topCards = new[] { overall, gateway, tasks, audit, session };
                var topColumns = contentWidth >= 1060 ? 5 : 3;
                var topCardWidth = (contentWidth - gap * (topColumns - 1)) / topColumns;
                var y = 232 + topExtra;
                for (var i = 0; i < topCards.Length; i++)
                {
                    var row = i / topColumns;
                    var col = i % topColumns;
                    topCards[i].SetBounds(margin + col * (topCardWidth + gap), y + row * 104, topCardWidth, 88);
                }
                y += ((topCards.Length + topColumns - 1) / topColumns) * 104 + 8;

                if (tokenHeader != null)
                {
                    tokenHeader.Visible = tokenSectionVisible;
                    tokenHeader.SetBounds(margin, y, contentWidth, 24);
                }
                if (tokenSectionVisible)
                {
                    var tokenCards = new[] { tokenTotal, tokenInput, tokenOutput, tokenCache, tokenCost };
                    var tokenColumns = contentWidth >= 1060 ? 5 : 3;
                    var tokenCardWidth = (contentWidth - gap * (tokenColumns - 1)) / tokenColumns;
                    var tokenY = y + 32;
                    for (var i = 0; i < tokenCards.Length; i++)
                    {
                        var row = i / tokenColumns;
                        var col = i % tokenColumns;
                        tokenCards[i].Panel.Visible = true;
                        tokenCards[i].SetBounds(margin + col * (tokenCardWidth + gap), tokenY + row * 96, tokenCardWidth, 84);
                    }
                    y = tokenY + ((tokenCards.Length + tokenColumns - 1) / tokenColumns) * 96 + 10;
                }
                else
                {
                    foreach (var card in new[] { tokenTotal, tokenInput, tokenOutput, tokenCache, tokenCost })
                    {
                        card.Panel.Visible = false;
                        card.SetBounds(margin, y, 1, 1);
                    }
                }

                if (costHintPopup != null)
                {
                    var hintWidth = Math.Min(530, contentWidth);
                    costHintPopup.Visible = false;
                    costHintPopup.SetBounds(margin, y, hintWidth, 56);
                }

                var gridHeight = 0;
                taskGrid.SetBounds(margin, y, contentWidth, gridHeight);
                taskGrid.Visible = false;
                if (taskHeader != null) taskHeader.Visible = false;
                y += 8;

                collabStatusLabel.SetBounds(margin, y, contentWidth, 20);
                y += 26;

                var halfWidth = (contentWidth - gap) / 2;
                MoveDirectLabelFromOriginalY(692, margin, y, halfWidth, 24);
                MoveDirectLabelFromOriginalX(622, margin + halfWidth + gap, y, halfWidth, 24);
                sessionList.SetBounds(margin, y + 30, halfWidth, 120);
                logList.SetBounds(margin + halfWidth + gap, y + 30, halfWidth, 120);
                y += 164;

                statusLine.SetBounds(margin, y, contentWidth, 24);
                legendLine.SetBounds(margin, y + 22, contentWidth, 22);
                AutoScrollMinSize = new Size(margin * 2 + contentWidth, y + 58);
            }
            finally
            {
                ResumeLayout();
            }
        }

        void MoveDirectLabelFromOriginalY(int originalY, int x, int y, int w, int h)
        {
            Label label = null;
            if (originalY == 344)
            {
                if (tokenHeader == null) tokenHeader = Controls.OfType<Label>().FirstOrDefault(l => l.Location.Y == originalY);
                label = tokenHeader;
            }
            else if (originalY == 486)
            {
                if (taskHeader == null) taskHeader = Controls.OfType<Label>().FirstOrDefault(l => l.Location.Y == originalY);
                label = taskHeader;
            }
            else if (originalY == 692)
            {
                if (sessionHeader == null) sessionHeader = Controls.OfType<Label>().FirstOrDefault(l => l.Location.Y == originalY && l.Location.X < 100);
                label = sessionHeader;
            }
            else
            {
                label = Controls.OfType<Label>().FirstOrDefault(l => l.Location.Y == originalY);
            }
            if (label != null) label.SetBounds(x, y, w, h);
        }

        void MoveDirectLabelFromOriginalX(int originalX, int x, int y, int w, int h)
        {
            if (logHeader == null) logHeader = Controls.OfType<Label>().FirstOrDefault(l => l.Location.X == originalX && l != updated);
            var label = logHeader;
            if (label != null) label.SetBounds(x, y, w, h);
        }

        ListBox MakeList(int x, int y, int w, int h)
        {
            return new ListBox
            {
                Location = new Point(x, y),
                Size = new Size(w, h),
                BackColor = Color.White,
                ForeColor = Color.FromArgb(31, 41, 55),
                BorderStyle = BorderStyle.None,
                Font = new Font("Microsoft YaHei UI", 9f)
            };
        }

        Label MakeLabel(string text, int x, int y, int w, int h, float size, Color color, bool bold)
        {
            return new Label
            {
                Text = text,
                Location = new Point(x, y),
                Size = new Size(w, h),
                ForeColor = color,
                BackColor = Color.Transparent,
                Font = new Font("Microsoft YaHei UI", size, bold ? FontStyle.Bold : FontStyle.Regular)
            };
        }

        async Task RefreshAsync()
        {
            if (refreshing) return;
            refreshing = true;
            if (togglingOpenClaw)
                updated.Text = lastOpenClawServiceActive ? "关闭中..." : "启动中...";
            try
            {
                var snapshot = await Task.Run(() => BuildSnapshot());
                Render(snapshot);
            }
            catch (Exception ex)
            {
                updated.Text = "刷新失败";
                statusLine.Text = ex.Message;
            }
            finally
            {
                refreshing = false;
                UpdateOpenClawPowerUi();
            }
        }

        async Task RefreshDiagnosticsAsync()
        {
            if (diagnosticsButton == null || !diagnosticsButton.Enabled) return;
            diagnosticsButton.Enabled = false;
            var oldText = diagnosticsButton.Text;
            diagnosticsButton.Text = "诊断中";
            try
            {
                var snapshot = await Task.Run(() => BuildDiagnosticsSnapshot());
                ShowDiagnosticsDialog(snapshot);
            }
            catch (Exception ex)
            {
                var snapshot = new DiagnosticsSnapshot { OverallState = "Warn" };
                snapshot.OverallReasons.Add("诊断读取失败：" + RedactSensitive(ex.Message));
                snapshot.Gateway.Add(new DiagnosticItem("诊断", "读取失败", "Warn", RedactSensitive(ex.Message), "local monitor"));
                ShowDiagnosticsDialog(snapshot);
            }
            finally
            {
                diagnosticsButton.Text = oldText;
                diagnosticsButton.Enabled = true;
            }
        }

        DiagnosticsSnapshot BuildDiagnosticsSnapshot()
        {
            var d = new DiagnosticsSnapshot();
            FillDiagnosticsGateway(d);
            FillDiagnosticsGatewayResilience(d);
            FillDiagnosticsNetworkStability(d);
            FillDiagnosticsTelegram(d);
            FillDiagnosticsSessions(d);
            FillDiagnosticsEntrancePressure(d);
            FillDiagnosticsTasksAndLogs(d);
            FinalizeDiagnosticsState(d);
            return d;
        }

        void FillDiagnosticsGateway(DiagnosticsSnapshot d)
        {
            try
            {
                var probe = GetGatewayProbeReadonly();
                if (probe.Item1)
                {
                    var root = AsDict(probe.Item2);
                    var ok = ToBool(Get(root, "ok"));
                    var target = AsDict(First(AsList(Get(root, "targets"))));
                    var connect = AsDict(Get(target, "connect"));
                    var rpcOk = ToBool(Get(connect, "rpcOk"));
                    var latency = ToLong(Get(connect, "latencyMs"));
                    var reachable = ok && rpcOk;
                    d.Gateway.Add(new DiagnosticItem("Reachable", reachable ? "yes" : "no", reachable ? "Good" : "Risk", reachable ? "gateway probe RPC 可达" : "gateway probe 未通过", "gateway probe --json"));
                    d.Gateway.Add(new DiagnosticItem("Latency", latency >= 0 ? latency + "ms" : "-", latency >= 0 && latency < 5000 ? "Good" : "Warn", latency >= 0 ? "probe 延迟" : "未读到延迟", "gateway probe --json"));
                }
                else
                {
                    d.Gateway.Add(new DiagnosticItem("Reachable", "读取失败", "Warn", RedactSensitive(probe.Item3), "gateway probe --json"));
                }

                var status = GetGatewayStatusReadonly();
                if (status.Item1)
                {
                    var text = RedactSensitive(status.Item2);
                    var running = Regex.IsMatch(text, @"Runtime:\s+running", RegexOptions.IgnoreCase);
                    var admin = Regex.IsMatch(text, @"Capability:\s+admin-capable", RegexOptions.IgnoreCase);
                    var pid = Regex.Match(text, @"pid\s+(\d+)", RegexOptions.IgnoreCase);
                    d.Gateway.Add(new DiagnosticItem("Runtime", running ? "running" : "需检查", running ? "Good" : "Risk", running ? "gateway runtime running" : "未看到 Runtime: running", "gateway status"));
                    d.Gateway.Add(new DiagnosticItem("Admin", admin ? "admin-capable" : "未知/受限", admin ? "Good" : "Warn", admin ? "管理能力可用" : "未看到 admin-capable", "gateway status"));
                    d.Gateway.Add(new DiagnosticItem("PID", pid.Success ? pid.Groups[1].Value : "-", pid.Success ? "Good" : "Warn", pid.Success ? "用于读取 ps 资源" : "未解析到 pid", "gateway status"));
                    if (pid.Success)
                    {
                        var proc = GetGatewayProcessReadonly(pid.Groups[1].Value);
                        d.Gateway.Add(new DiagnosticItem("CPU/RSS", proc.Item1 ? RedactSensitive(proc.Item2) : "读取失败", proc.Item1 ? "Good" : "Warn", proc.Item1 ? "ps 读取成功" : RedactSensitive(proc.Item3), "ps"));
                    }
                }
                else
                {
                    d.Gateway.Add(new DiagnosticItem("Runtime", "读取失败", "Warn", RedactSensitive(status.Item3), "gateway status"));
                }

                var stability = GetGatewayStabilityReadonly();
                if (stability.Item1)
                {
                    var text = RedactSensitive(stability.Item2);
                    var warn = Regex.IsMatch(text, "warning|liveness|pressure=[1-9]", RegexOptions.IgnoreCase);
                    var first = FirstMeaningfulLine(text, "Gateway Stability");
                    d.Gateway.Add(new DiagnosticItem("Stability", warn ? "需观察" : "正常", warn ? "Warn" : "Good", Trim(first, 120), "gateway stability"));
                }
                else
                {
                    d.Gateway.Add(new DiagnosticItem("Stability", "读取失败", "Warn", RedactSensitive(stability.Item3), "gateway stability"));
                }
            }
            catch (Exception ex)
            {
                d.Gateway.Add(new DiagnosticItem("Gateway", "读取失败", "Warn", RedactSensitive(ex.Message), "diagnostics"));
            }
        }

        void FillDiagnosticsGatewayResilience(DiagnosticsSnapshot d)
        {
            try
            {
                var gatewayPid = "";
                var gatewayUptime = "";
                var gatewayStarted = "";
                var proc = GetGatewayProcessSnapshotReadonly();
                if (proc.Item1)
                {
                    var parts = Regex.Split(proc.Item2.Trim(), @"\s+").Where(x => !string.IsNullOrWhiteSpace(x)).ToArray();
                    if (parts.Length >= 11)
                    {
                        var pid = parts[0];
                        var ppid = parts[1];
                        var etime = parts[2];
                        var cpu = parts[3];
                        var mem = parts[4];
                        var rss = parts[5];
                        var started = string.Join(" ", parts.Skip(6).Take(5).ToArray());
                        double cpuValue = ToDouble(cpu);
                        double rssMb = ToDouble(rss) / 1024.0;
                        var changed = !string.IsNullOrWhiteSpace(lastDiagnosticsGatewayPid) && lastDiagnosticsGatewayPid != pid;
                        lastDiagnosticsGatewayPid = pid;
                        gatewayPid = pid;
                        gatewayUptime = etime;
                        gatewayStarted = started;

                        d.GatewayResilience.Add(new DiagnosticItem("Gateway PID", pid + " / ppid " + ppid, changed ? "Warn" : "Good", changed ? "本次诊断发现 PID 与上次不同" : "当前 gateway 进程", "ps"));
                        d.GatewayResilience.Add(new DiagnosticItem("Gateway uptime", etime, "Good", "当前进程运行时长", "ps"));
                        d.GatewayResilience.Add(new DiagnosticItem("Gateway started", started, "Good", "当前进程启动时间", "ps"));
                        d.GatewayResilience.Add(new DiagnosticItem("Gateway CPU/RSS", cpu + "% / " + Math.Round(rssMb).ToString("0", CultureInfo.InvariantCulture) + "MB", cpuValue >= 100 || rssMb >= 2048 ? "Risk" : cpuValue >= 50 || rssMb >= 1024 ? "Warn" : "Good", "CPU >50% 或 RSS >1GB 需观察；CPU >100% 或 RSS >2GB 高风险", "ps"));
                    }
                    else
                    {
                        d.GatewayResilience.Add(new DiagnosticItem("Gateway process", "读取失败", "Warn", "ps 输出无法解析", "ps"));
                    }
                }
                else
                {
                    d.GatewayResilience.Add(new DiagnosticItem("Gateway process", "读取失败", "Warn", RedactSensitive(proc.Item3), "ps"));
                }

                var stability = GetGatewayStabilityFilesReadonly();
                if (stability.Item1 && !string.IsNullOrWhiteSpace(stability.Item2))
                {
                    var count = 0;
                    var rows = new List<DiagnosticItem>();
                    var latestReason = "";
                    var latestPid = "";
                    var latestTimestamp = "";
                    var latestName = "";
                    var timelineState = "Good";
                    foreach (var line in stability.Item2.Split(new[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries).Take(5))
                    {
                        var columns = line.Split(new[] { '\t' }, 2);
                        var modified = columns.Length > 0 ? columns[0] : "-";
                        var name = columns.Length > 1 ? columns[1] : line;
                        var match = Regex.Match(name, @"^openclaw-stability-(.+)-(\d+)-(.+)\.json$", RegexOptions.IgnoreCase);
                        var timestamp = match.Success ? match.Groups[1].Value : modified;
                        var pid = match.Success ? match.Groups[2].Value : "-";
                        var reason = match.Success ? match.Groups[3].Value : name;
                        var recoveredToCurrentGateway = !string.IsNullOrWhiteSpace(gatewayPid) && pid != "-" && pid != gatewayPid;
                        var state = StabilityEventState(reason, pid, gatewayPid);
                        timelineState = CombineDiagnosticState(timelineState, state);
                        if (count == 0)
                        {
                            latestReason = reason;
                            latestPid = pid;
                            latestTimestamp = timestamp;
                            latestName = name;
                        }
                        rows.Add(new DiagnosticItem("Stability file", reason + " · pid " + pid, state, StabilityEventDetail(timestamp, name) + (recoveredToCurrentGateway ? " · 当前 gateway 已恢复到 pid " + gatewayPid : ""), "stability files"));
                        count++;
                    }
                    if (count > 0)
                    {
                        var timeline = "current pid " + (string.IsNullOrWhiteSpace(gatewayPid) ? "unknown" : gatewayPid);
                        if (!string.IsNullOrWhiteSpace(gatewayUptime)) timeline += " · uptime " + gatewayUptime;
                        if (!string.IsNullOrWhiteSpace(gatewayStarted)) timeline += " · started " + gatewayStarted;
                        var recoveredLatest = !string.IsNullOrWhiteSpace(gatewayPid) && latestPid != "-" && latestPid != gatewayPid;
                        var latestDetail = StabilityEventDetail(latestTimestamp, latestName) + (recoveredLatest ? " · 当前 gateway 已恢复到 pid " + gatewayPid : "");
                        d.GatewayResilience.Add(new DiagnosticItem("Restart timeline", timeline, timelineState, "latest stability: " + latestReason + " · pid " + latestPid + " · " + latestDetail, "ps + stability files"));
                        foreach (var row in rows) d.GatewayResilience.Add(row);
                    }
                    else
                    {
                        d.GatewayResilience.Add(new DiagnosticItem("Restart timeline", string.IsNullOrWhiteSpace(gatewayPid) ? "无当前 PID" : "current pid " + gatewayPid, "Good", "未发现 stability json", "ps + stability files"));
                        d.GatewayResilience.Add(new DiagnosticItem("Stability files", "无记录", "Good", "未发现 stability json", "stability files"));
                    }
                }
                else if (stability.Item1)
                {
                    d.GatewayResilience.Add(new DiagnosticItem("Restart timeline", string.IsNullOrWhiteSpace(gatewayPid) ? "无当前 PID" : "current pid " + gatewayPid, "Good", "未发现 stability json", "ps + stability files"));
                    d.GatewayResilience.Add(new DiagnosticItem("Stability files", "无记录", "Good", "未发现 stability json", "stability files"));
                }
                else
                {
                    d.GatewayResilience.Add(new DiagnosticItem("Stability files", "读取失败", "Warn", RedactSensitive(stability.Item3), "stability files"));
                }

                var residual = GetOpenClawTasksResidualProcessesReadonly();
                if (residual.Item1 && !string.IsNullOrWhiteSpace(residual.Item2))
                {
                    foreach (var line in residual.Item2.Split(new[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries).Take(8))
                    {
                        var parts = Regex.Split(line.Trim(), @"\s+").Where(x => !string.IsNullOrWhiteSpace(x)).ToArray();
                        if (parts.Length < 7) continue;
                        var pid = parts[0];
                        var cpu = parts[2];
                        var rss = parts[4];
                        var etime = parts[5];
                        var args = string.Join(" ", parts.Skip(6).ToArray());
                        double rssMb = ToDouble(rss) / 1024.0;
                        var risky = ToDouble(cpu) >= 50 || ProcessElapsedOverMinutes(etime, 2);
                        d.GatewayResilience.Add(new DiagnosticItem("openclaw-tasks residual", "pid " + pid + " · CPU " + cpu + "% · RSS " + Math.Round(rssMb).ToString("0", CultureInfo.InvariantCulture) + "MB · " + etime, risky ? "Warn" : "Good", Trim(args, 120), "ps only"));
                    }
                }
                else if (residual.Item1)
                {
                    d.GatewayResilience.Add(new DiagnosticItem("openclaw-tasks residual", "未发现", "Good", "未检测到 openclaw-tasks 残留进程", "ps only"));
                }
                else
                {
                    d.GatewayResilience.Add(new DiagnosticItem("openclaw-tasks residual", "读取失败", "Warn", RedactSensitive(residual.Item3), "ps only"));
                }
            }
            catch (Exception ex)
            {
                d.GatewayResilience.Add(new DiagnosticItem("Gateway Resilience", "读取失败", "Warn", RedactSensitive(ex.Message), "diagnostics"));
            }
        }

        void FillDiagnosticsNetworkStability(DiagnosticsSnapshot d)
        {
            try
            {
                var status = GetNetwatchStatusReadonly();
                if (!status.Item1)
                {
                    d.NetworkStability.Add(new DiagnosticItem("Netwatch", "读取失败", "Warn", RedactSensitive(status.Item3), "systemd/user files"));
                    return;
                }

                var values = ParseKeyValueLines(status.Item2);
                var installed = GetValue(values, "installed");
                var timerActive = GetValue(values, "timer_active");
                var timerEnabled = GetValue(values, "timer_enabled");
                var serviceActive = GetValue(values, "service_active");
                var mode = GetValue(values, "mode");
                var previous = GetValue(values, "previous");
                var offlineCount = GetValue(values, "offline_count");
                var gatewayFailCount = GetValue(values, "gateway_fail_count");
                var lastRestart = GetValue(values, "last_restart");
                var lastLog = GetValue(values, "last_log");

                var isInstalled = installed == "yes";
                d.NetworkStability.Add(new DiagnosticItem("Netwatch installed", isInstalled ? "yes" : "no", isInstalled ? "Good" : "Warn", isInstalled ? "控制中心可见；后台执行层已安装" : "尚未安装网络稳定性 watchdog；可通过 openclaw-netwatch 安装", "systemd/user files"));

                if (!isInstalled) return;

                var active = timerActive == "active";
                d.NetworkStability.Add(new DiagnosticItem("Netwatch timer", timerActive + " / " + timerEnabled, active ? "Good" : "Warn", active ? "systemd user timer 正在运行" : "timer 未 active；不会持续观察网络恢复", "systemctl --user"));

                var modeState = mode == "recover" ? "Warn" : "Good";
                var modeReason = mode == "recover" ? "检测到旧 recover 配置；新版 netwatch 已收敛为 observe-only，不应自动重启 gateway" : "Observe-only：只记录，不重启 gateway";
                d.NetworkStability.Add(new DiagnosticItem("Netwatch mode", string.IsNullOrWhiteSpace(mode) ? "unknown" : mode, modeState, modeReason, "~/.config/openclaw-netwatch.env"));

                if (!string.IsNullOrWhiteSpace(serviceActive))
                {
                    d.NetworkStability.Add(new DiagnosticItem("Last service state", serviceActive, serviceActive == "failed" ? "Warn" : "Good", "oneshot service 最近状态；timer active 比单次 service active 更重要", "systemctl --user"));
                }

                var counters = "network=" + (string.IsNullOrWhiteSpace(previous) ? "unknown" : previous) + " · offline_count=" + (string.IsNullOrWhiteSpace(offlineCount) ? "0" : offlineCount) + " · gateway_fail_count=" + (string.IsNullOrWhiteSpace(gatewayFailCount) ? "0" : gatewayFailCount);
                var counterWarn = ToLong(offlineCount) > 0 || ToLong(gatewayFailCount) > 0 || previous == "offline";
                d.NetworkStability.Add(new DiagnosticItem("Netwatch state", counters, counterWarn ? "Warn" : "Good", "最近一次网络/gateway 观察状态", "~/.cache/openclaw-netwatch/state"));

                if (!string.IsNullOrWhiteSpace(lastRestart) && lastRestart != "0")
                {
                    d.NetworkStability.Add(new DiagnosticItem("Last recovery note", lastRestart, "Good", "netwatch 记录的最近一次恢复建议 epoch；不会主动重启 gateway", "~/.cache/openclaw-netwatch/state"));
                }

                if (!string.IsNullOrWhiteSpace(lastLog))
                {
                    d.NetworkStability.Add(new DiagnosticItem("Last netwatch log", Trim(RedactSensitive(lastLog), 180), "Good", "最近 watchdog 事件", "~/.cache/openclaw-netwatch/watchdog.log"));
                }
                else
                {
                    d.NetworkStability.Add(new DiagnosticItem("Last netwatch log", "无记录", "Good", "尚无 watchdog 日志", "~/.cache/openclaw-netwatch/watchdog.log"));
                }
            }
            catch (Exception ex)
            {
                d.NetworkStability.Add(new DiagnosticItem("Network Stability", "读取失败", "Warn", RedactSensitive(ex.Message), "diagnostics"));
            }
        }

        void FillDiagnosticsTelegram(DiagnosticsSnapshot d)
        {
            try
            {
                var channels = GetChannelsStatusReadonly();
                if (channels.Item1)
                {
                    var data = AsDict(channels.Item2);
                    var accountsByChannel = AsDict(Get(data, "channelAccounts"));
                    var telegramAccounts = AsList(Get(accountsByChannel, "telegram"));
                    var account = telegramAccounts.Count > 0 ? AsDict(telegramAccounts[0]) : new Dictionary<string, object>();
                    var channelsRoot = AsDict(Get(data, "channels"));
                    var telegram = AsDict(Get(channelsRoot, "telegram"));
                    var source = account.Count > 0 ? account : telegram;
                    var configured = ToBool(Get(source, "configured"));
                    var running = ToBool(Get(source, "running"));
                    var connected = ToBool(Get(source, "connected"));
                    var lastInboundAt = ToLong(Get(source, "lastInboundAt"));
                    var lastOutboundAt = ToLong(Get(source, "lastOutboundAt"));
                    var inboundSeen = lastInboundAt > 0;
                    var outboundSeen = lastOutboundAt > 0;
                    var ok = configured && running && connected;
                    var channelValue = !configured ? "未配置" : !running ? "未运行" : !connected ? "未连接" : outboundSeen ? "已回复" : inboundSeen ? "已收未回证" : "已连接未验证";
                    var channelState = ok ? (outboundSeen ? "Good" : "Warn") : "Risk";
                    d.Telegram.Add(new DiagnosticItem("Channel", channelValue, channelState, "configured=" + configured + ", running=" + running + ", connected=" + connected + ", inboundSeen=" + inboundSeen + ", outboundSeen=" + outboundSeen, "channels status --json"));

                    var eventLoop = AsDict(Get(data, "eventLoop"));
                    if (eventLoop.Count > 0)
                    {
                        var degraded = ToBool(Get(eventLoop, "degraded"));
                        var reasons = string.Join(",", AsList(Get(eventLoop, "reasons")).Cast<object>().Select(x => Convert.ToString(x)));
                        var utilization = Convert.ToString(Get(eventLoop, "utilization") ?? "-");
                        var cpuCoreRatio = Convert.ToString(Get(eventLoop, "cpuCoreRatio") ?? "-");
                        d.Telegram.Add(new DiagnosticItem("Entrance pressure", degraded ? "在线但可能慢" : "正常", degraded ? "Warn" : "Good", "eventLoop.degraded=" + degraded + ", reasons=" + reasons + ", utilization=" + utilization + ", cpuCoreRatio=" + cpuCoreRatio, "channels status --json eventLoop"));
                    }
                }
                else
                {
                    d.Telegram.Add(new DiagnosticItem("Channel", "读取失败", "Warn", RedactSensitive(channels.Item3), "channels status --json"));
                }

                var bindings = GetAgentsBindingsReadonly();
                if (bindings.Item1)
                {
                    var text = RedactSensitive(bindings.Item2);
                    var ok = text.Contains("telegram <- telegram") && text.Contains("accountId=default");
                    d.Telegram.Add(new DiagnosticItem("Binding", ok ? "telegram:default -> telegram" : "需检查", ok ? "Good" : "Risk", ok ? "Telegram 默认入口路由到 telegram agent" : Trim(text, 120), "agents bindings"));
                }
                else
                {
                    d.Telegram.Add(new DiagnosticItem("Binding", "读取失败", "Warn", RedactSensitive(bindings.Item3), "agents bindings"));
                }
            }
            catch (Exception ex)
            {
                d.Telegram.Add(new DiagnosticItem("Telegram", "读取失败", "Warn", RedactSensitive(ex.Message), "diagnostics"));
            }
        }

        void FillDiagnosticsSessions(DiagnosticsSnapshot d)
        {
            try
            {
                var sessions = GetSessionsActive24hReadonly();
                if (!sessions.Item1)
                {
                    d.Sessions.Add(new DiagnosticItem("Sessions", "读取失败", "Warn", RedactSensitive(sessions.Item3), "sessions --json"));
                    d.Telegram.Add(new DiagnosticItem("Current sessionKey", "未知", "Warn", "sessions 读取失败", "sessions --json"));
                    return;
                }

                var root = AsDict(sessions.Item2);
                var items = AsList(Get(root, "sessions"));
                var mainCount = 0;
                var telegramCount = 0;
                var high = new List<Dictionary<string, object>>();
                Dictionary<string, object> currentTelegram = null;
                Dictionary<string, object> legacyMainTelegram = null;

                foreach (var item in items)
                {
                    var row = AsDict(item);
                    var agentId = Convert.ToString(Get(row, "agentId") ?? "");
                    var key = Convert.ToString(Get(row, "key") ?? "");
                    var total = Math.Max(0, ToLong(Get(row, "totalTokens")));
                    if (agentId == "main") mainCount++;
                    if (agentId == "telegram") telegramCount++;
                    if (total >= 120000) high.Add(row);
                    if (key.StartsWith("agent:telegram:telegram:", StringComparison.OrdinalIgnoreCase))
                    {
                        if (currentTelegram == null || ToLong(Get(row, "ageMs")) < ToLong(Get(currentTelegram, "ageMs"))) currentTelegram = row;
                    }
                    if (key.StartsWith("agent:main:telegram:", StringComparison.OrdinalIgnoreCase))
                    {
                        if (legacyMainTelegram == null || ToLong(Get(row, "ageMs")) < ToLong(Get(legacyMainTelegram, "ageMs"))) legacyMainTelegram = row;
                    }
                }

                d.Sessions.Add(new DiagnosticItem("Active 24h", items.Count.ToString(), items.Count <= 25 ? "Good" : "Warn", "24h 活跃 session 数", "sessions --all-agents --active 1440 --json"));
                d.Sessions.Add(new DiagnosticItem("Agent 分布", "main " + mainCount + " / telegram " + telegramCount, telegramCount > 0 ? "Good" : "Warn", "确认 Telegram 与 main 分离", "sessions --json"));

                if (currentTelegram != null)
                {
                    var key = RedactSensitive(Convert.ToString(Get(currentTelegram, "key") ?? ""));
                    var total = Math.Max(0, ToLong(Get(currentTelegram, "totalTokens")));
                    var context = Math.Max(0, ToLong(Get(currentTelegram, "contextTokens")));
                    var percent = context > 0 ? (int)Math.Round(total * 100.0 / context) : -1;
                    d.Telegram.Add(new DiagnosticItem("Current sessionKey", key, key.StartsWith("agent:telegram:telegram:") ? "Good" : "Risk", "当前 Telegram 入口 session", "sessions --json"));
                    d.Telegram.Add(new DiagnosticItem("Telegram token", FormatTokens(total) + (context > 0 ? " / " + FormatTokens(context) + " (" + percent + "%)" : ""), TelegramTokenState(total), TelegramTokenReason(total), "sessions --json"));
                }
                else
                {
                    d.Telegram.Add(new DiagnosticItem("Current sessionKey", "未找到", "Warn", "24h sessions 中未找到 agent:telegram:telegram", "sessions --json"));
                }

                if (legacyMainTelegram != null)
                {
                    var age = Age(ToLong(Get(legacyMainTelegram, "ageMs")));
                    d.Sessions.Add(new DiagnosticItem("旧 main Telegram", age, "Warn", "发现旧 agent:main:telegram session；若不再承接新消息则只是历史残留", "sessions --json"));
                }

                foreach (var row in high.OrderByDescending(r => ToLong(Get(r, "totalTokens"))).Take(6))
                {
                    var key = RedactSensitive(Convert.ToString(Get(row, "key") ?? ""));
                    var total = Math.Max(0, ToLong(Get(row, "totalTokens")));
                    var isTelegramEntry = key.StartsWith("agent:telegram:telegram:", StringComparison.OrdinalIgnoreCase);
                    var state = isTelegramEntry ? TelegramTokenState(total) : total >= 220000 ? "Risk" : total >= 180000 ? "Risk" : "Warn";
                    var reason = isTelegramEntry ? TelegramTokenReason(total) : total >= 220000 ? "220K+ 严重高上下文压力" : total >= 180000 ? "180K+ 高上下文压力" : "120K+ 需观察";
                    d.Sessions.Add(new DiagnosticItem("High token", FormatTokens(total) + " · " + Trim(key, 80), state, reason, "sessions --json"));
                }
            }
            catch (Exception ex)
            {
                d.Sessions.Add(new DiagnosticItem("Sessions", "读取失败", "Warn", RedactSensitive(ex.Message), "diagnostics"));
            }
        }

        void FillDiagnosticsEntrancePressure(DiagnosticsSnapshot d)
        {
            try
            {
                var logs = GetEntrancePressureLogsReadonly();
                if (!logs.Item1)
                {
                    d.EntrancePressure.Add(new DiagnosticItem("Entrance pressure", "读取失败", "Warn", RedactSensitive(logs.Item3), "journalctl"));
                    return;
                }

                var text = RedactSensitive(logs.Item2);
                if (string.IsNullOrWhiteSpace(text))
                {
                    d.EntrancePressure.Add(new DiagnosticItem("Recent pressure", "未发现", "Good", "最近 30 分钟未命中入口压力关键字", "journalctl"));
                    return;
                }

                var livenessCount = CountMatches(text, @"liveness warning|event_loop_delay|event_loop_utilization");
                var severeLoopCount = CountMatches(text, @"event_loop_utilization,cpu|cpuCoreRatio=1|eventLoopUtilization=0\.(?:8|9)|eventLoopDelayMaxMs=(?:[5-9]\d{3}|\d{5,})");
                var memoryCount = CountMatches(text, @"memory-core|dreaming");
                var memoryFailureCount = CountMatches(text, @"narrative generation ended with status=timeout|dreaming cleanup scrub failed|session file locked");
                var cleanupCount = CountMatches(text, @"agent cleanup timed out|pi-trajectory-flush");
                var fetchCount = CountMatches(text, @"fetch timeout|sync failed|TypeError: fetch failed");
                var telegramOkCount = CountMatches(text, @"\[telegram\] sendMessage ok|message\.processed.*channel=telegram");
                var telegramErrorCount = CountMatches(text, @"\[telegram\].*(failed|error|timeout)|sendMessage failed");

                var loopState = severeLoopCount > 0 ? "Risk" : livenessCount > 0 ? "Warn" : "Good";
                d.EntrancePressure.Add(new DiagnosticItem(
                    "Gateway loop",
                    livenessCount == 0 ? "无明显压力" : livenessCount + " warnings" + (severeLoopCount > 0 ? " / " + severeLoopCount + " severe" : ""),
                    loopState,
                    livenessCount == 0 ? "最近未见 event-loop/liveness 告警" : "Gateway event loop 有卡顿；Telegram 可能在线但回包慢",
                    "journalctl 30m"));

                var memoryState = memoryFailureCount > 0 || cleanupCount > 1 ? "Risk" : memoryCount > 0 || cleanupCount > 0 ? "Warn" : "Good";
                d.EntrancePressure.Add(new DiagnosticItem(
                    "Memory / cleanup",
                    "memory " + memoryCount + " / failure " + memoryFailureCount + " / cleanup " + cleanupCount,
                    memoryState,
                    memoryFailureCount > 0 ? "memory-core/dreaming 或 session lock 正在影响入口共享 gateway" : cleanupCount > 0 ? "agent cleanup 超时会短暂拖慢入口" : "未见 memory-core/cleanup 压力",
                    "journalctl 30m"));

                d.EntrancePressure.Add(new DiagnosticItem(
                    "Provider / fetch",
                    fetchCount == 0 ? "无近期失败" : fetchCount + " failures",
                    fetchCount > 0 ? "Warn" : "Good",
                    fetchCount > 0 ? "provider/network fetch timeout 会表现为 Telegram 等待或失败" : "未见 fetch timeout/sync failed",
                    "journalctl 30m"));

                d.EntrancePressure.Add(new DiagnosticItem(
                    "Telegram delivery",
                    telegramOkCount > 0 ? telegramOkCount + " recent ok" : "无近期发送记录",
                    telegramErrorCount > 0 ? "Risk" : "Good",
                    telegramErrorCount > 0 ? "存在 Telegram 发送失败/超时日志" : telegramOkCount > 0 ? "最近有 Telegram sendMessage/message.processed 成功记录" : "没有近期发送记录不等于异常，可能只是没有回复输出",
                    "journalctl 30m"));

                var lastPressure = LastMatchingLine(text, @"liveness warning|memory-core|dreaming|session file locked|cleanup timed out|fetch timeout|sync failed|\[telegram\]");
                if (!string.IsNullOrWhiteSpace(lastPressure))
                {
                    d.EntrancePressure.Add(new DiagnosticItem("Latest signal", Trim(lastPressure, 220), severeLoopCount > 0 || memoryFailureCount > 0 || telegramErrorCount > 0 ? "Warn" : "Good", "最近一条入口相关信号", "journalctl 30m"));
                }
            }
            catch (Exception ex)
            {
                d.EntrancePressure.Add(new DiagnosticItem("Entrance pressure", "读取失败", "Warn", RedactSensitive(ex.Message), "diagnostics"));
            }
        }

        void FillDiagnosticsTasksAndLogs(DiagnosticsSnapshot d)
        {
            try
            {
                var tasks = GetTasksListReadonly();
                if (tasks.Item1)
                {
                    var root = AsDict(tasks.Item2);
                    var items = AsList(Get(root, "tasks"));
                    var running = 0; var queued = 0; var failed = 0; var timedOut = 0; var lost = 0;
                    foreach (var item in items)
                    {
                        var status = (Convert.ToString(Get(AsDict(item), "status") ?? "") ?? "").ToLowerInvariant();
                        if (status == "running") running++;
                        else if (status == "queued") queued++;
                        else if (status == "failed") failed++;
                        else if (status == "timed_out") timedOut++;
                        else if (status == "lost") lost++;
                    }
                    d.TasksLogs.Add(new DiagnosticItem("Tasks", "running " + running + " / queued " + queued, queued == 0 ? "Good" : "Warn", "后台任务压力", "tasks list --json"));
                    d.TasksLogs.Add(new DiagnosticItem("Task issues", "failed " + failed + " / timed_out " + timedOut + " / lost " + lost, (failed + timedOut + lost) > 20 ? "Warn" : "Good", "历史/近期任务异常计数", "tasks list --json"));
                }
                else
                {
                    d.TasksLogs.Add(new DiagnosticItem("Tasks", "读取失败", "Warn", RedactSensitive(tasks.Item3), "tasks list --json"));
                }

                d.TasksLogs.Add(new DiagnosticItem("Audit", "已降级", "Good", "诊断 v0 不再调用 tasks audit/show，避免只读诊断残留高 CPU 进程", "disabled"));
                d.TasksLogs.Add(new DiagnosticItem("Logs", "已降级", "Good", "诊断 v0 不再调用 logs.tail；重启证据改看 Gateway Resilience/stability 文件", "disabled"));
                d.TasksLogs.Add(new DiagnosticItem("Keyword hits", "未扫描", "Good", "为保护 Telegram 入口，关键字日志扫描后续改为文件级轻量读取", "disabled"));
            }
            catch (Exception ex)
            {
                d.TasksLogs.Add(new DiagnosticItem("Tasks & Logs", "读取失败", "Warn", RedactSensitive(ex.Message), "diagnostics"));
            }
        }

        void FinalizeDiagnosticsState(DiagnosticsSnapshot d)
        {
            var all = d.Gateway.Concat(d.GatewayResilience).Concat(d.NetworkStability).Concat(d.EntrancePressure).Concat(d.Telegram).Concat(d.Sessions).Concat(d.TasksLogs).ToList();
            var risk = all.Where(i => i.State == "Risk").ToList();
            var warn = all.Where(i => i.State == "Warn").ToList();
            d.OverallState = risk.Count > 0 ? "HighRisk" : warn.Count > 0 ? "Observe" : "Normal";
            foreach (var item in risk.Take(4)) d.OverallReasons.Add(item.Label + ": " + item.Reason);
            if (d.OverallReasons.Count == 0) foreach (var item in warn.Take(4)) d.OverallReasons.Add(item.Label + ": " + item.Reason);
            if (d.OverallReasons.Count == 0) d.OverallReasons.Add("诊断项未发现明显风险。");
        }

        Tuple<bool, object, string> GetGatewayProbeReadonly()
        {
            return RunOpenClawJson(new[] { "gateway", "probe", "--json", "--timeout", "8000" }, 12000);
        }

        Tuple<bool, string, string> GetGatewayStatusReadonly()
        {
            return RunOpenClawText(new[] { "gateway", "status" }, 12000);
        }

        Tuple<bool, string, string> GetGatewayStabilityReadonly()
        {
            return RunOpenClawText(new[] { "gateway", "stability" }, 12000);
        }

        Tuple<bool, string, string> GetGatewayProcessReadonly(string pid)
        {
            if (!Regex.IsMatch(pid ?? "", @"^\d+$")) return Tuple.Create(false, "", "pid 非数字");
            var script = "ps -p " + pid + " -o pid=,etime=,pcpu=,rss= 2>/dev/null | head -1";
            var result = RunProcess("wsl.exe", new[] { "-d", WslDistro, "--", "bash", "-lc", script }, 8000);
            if (!result.Ok) return Tuple.Create(false, "", RedactSensitive(result.Stderr + result.Error));
            var parts = Regex.Split(result.Stdout.Trim(), @"\s+").Where(x => !string.IsNullOrWhiteSpace(x)).ToArray();
            if (parts.Length < 4) return Tuple.Create(false, "", "ps 输出无法解析");
            double rssKb;
            double.TryParse(parts[3], NumberStyles.Float, CultureInfo.InvariantCulture, out rssKb);
            var text = "pid " + parts[0] + " · uptime " + parts[1] + " · CPU " + parts[2] + "% · RSS " + Math.Round(rssKb / 1024.0).ToString("0", CultureInfo.InvariantCulture) + "MB";
            return Tuple.Create(true, text, "");
        }

        Tuple<bool, string, string> GetGatewayProcessSnapshotReadonly()
        {
            var script =
                "ps -eo pid=,ppid=,etime=,pcpu=,pmem=,rss=,lstart=,args= 2>/dev/null | grep -F 'openclaw/dist/index.js gateway --port 18789' | grep -v grep | head -1";
            var result = RunProcess("wsl.exe", new[] { "-d", WslDistro, "--", "bash", "-lc", script }, 5000);
            return Tuple.Create(result.Ok, result.Stdout, result.Stderr + result.Error);
        }

        Tuple<bool, string, string> GetGatewayStabilityFilesReadonly()
        {
            var script =
                "if [ ! -d ~/.openclaw/logs/stability ]; then exit 0; fi\n" +
                "find ~/.openclaw/logs/stability -maxdepth 1 -type f -name '*.json' -printf '%T@\\t%f\\n' 2>/dev/null | sort -nr | head -5";
            var result = RunProcess("wsl.exe", new[] { "-d", WslDistro, "--", "bash", "-lc", script }, 3000);
            return Tuple.Create(result.Ok, result.Stdout, result.Stderr + result.Error);
        }

        string StabilityEventState(string reason, string eventPid, string currentGatewayPid)
        {
            var serious = Regex.IsMatch(reason ?? "", "stop_shutdown_timeout|SIGKILL|killed|startup_failed", RegexOptions.IgnoreCase);
            var warning = Regex.IsMatch(reason ?? "", "restart|SIGTERM|timeout", RegexOptions.IgnoreCase);
            var currentGatewayRunning = !string.IsNullOrWhiteSpace(currentGatewayPid);
            var eventBelongsToPreviousGateway = currentGatewayRunning && !string.IsNullOrWhiteSpace(eventPid) && eventPid != "-" && eventPid != currentGatewayPid;
            if (serious) return eventBelongsToPreviousGateway ? "Warn" : "Risk";
            if (warning) return "Warn";
            return "Good";
        }

        static string CombineDiagnosticState(string current, string next)
        {
            if (current == "Risk" || next == "Risk") return "Risk";
            if (current == "Warn" || next == "Warn") return "Warn";
            if (current == "Confirm" || next == "Confirm") return "Confirm";
            if (current == "Unknown" || next == "Unknown") return "Unknown";
            return "Good";
        }

        string StabilityEventDetail(string timestamp, string name)
        {
            var ageMs = StabilityTimestampAgeMs(timestamp);
            var age = ageMs >= 0 ? " · " + Age(ageMs) + "前" : "";
            return timestamp + age + " · " + name;
        }

        static long StabilityTimestampAgeMs(string value)
        {
            if (string.IsNullOrWhiteSpace(value)) return -1;
            value = value.Trim();
            double epochSeconds;
            if (double.TryParse(value, NumberStyles.Float, CultureInfo.InvariantCulture, out epochSeconds) && epochSeconds > 0)
            {
                var eventMs = (long)(epochSeconds * 1000.0);
                return Math.Max(0, DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() - eventMs);
            }

            DateTimeOffset parsed;
            if (DateTimeOffset.TryParseExact(value, "yyyy-MM-dd'T'HH-mm-ss-fff'Z'", CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out parsed))
            {
                return (long)Math.Max(0, (DateTimeOffset.UtcNow - parsed).TotalMilliseconds);
            }
            return -1;
        }

        Tuple<bool, string, string> GetOpenClawTasksResidualProcessesReadonly()
        {
            var script = "ps -eo pid=,ppid=,pcpu=,pmem=,rss=,etime=,args= 2>/dev/null | grep -E '[o]penclaw tasks|[o]penclaw-tasks' | head -8";
            var result = RunProcess("wsl.exe", new[] { "-d", WslDistro, "--", "bash", "-lc", script }, 5000);
            if (!result.Ok && string.IsNullOrWhiteSpace(result.Stdout)) return Tuple.Create(true, "", "");
            return Tuple.Create(result.Ok, result.Stdout, result.Stderr + result.Error);
        }

        Tuple<bool, string, string> GetNetwatchStatusReadonly()
        {
            var script =
                "installed=no\n" +
                "[ -x ~/.local/bin/openclaw-netwatch ] && [ -f ~/.config/systemd/user/openclaw-netwatch.timer ] && installed=yes\n" +
                "mode=missing\n" +
                "if [ -r ~/.config/openclaw-netwatch.env ]; then mode=$(sed -n 's/^OPENCLAW_NETWATCH_MODE=//p' ~/.config/openclaw-netwatch.env | tail -1); fi\n" +
                "timer_active=$(systemctl --user is-active openclaw-netwatch.timer 2>/dev/null || true)\n" +
                "timer_enabled=$(systemctl --user is-enabled openclaw-netwatch.timer 2>/dev/null || true)\n" +
                "service_active=$(systemctl --user is-active openclaw-netwatch.service 2>/dev/null || true)\n" +
                "previous=\n" +
                "offline_count=0\n" +
                "gateway_fail_count=0\n" +
                "last_restart=0\n" +
                "if [ -r ~/.cache/openclaw-netwatch/state ]; then . ~/.cache/openclaw-netwatch/state 2>/dev/null || true; fi\n" +
                "last_log=\n" +
                "if [ -r ~/.cache/openclaw-netwatch/watchdog.log ]; then last_log=$(tail -1 ~/.cache/openclaw-netwatch/watchdog.log | tr '\\t' ' '); fi\n" +
                "printf 'installed=%s\\n' \"$installed\"\n" +
                "printf 'mode=%s\\n' \"$mode\"\n" +
                "printf 'timer_active=%s\\n' \"$timer_active\"\n" +
                "printf 'timer_enabled=%s\\n' \"$timer_enabled\"\n" +
                "printf 'service_active=%s\\n' \"$service_active\"\n" +
                "printf 'previous=%s\\n' \"$previous\"\n" +
                "printf 'offline_count=%s\\n' \"$offline_count\"\n" +
                "printf 'gateway_fail_count=%s\\n' \"$gateway_fail_count\"\n" +
                "printf 'last_restart=%s\\n' \"$last_restart\"\n" +
                "printf 'last_log=%s\\n' \"$last_log\"";
            var result = RunProcess("wsl.exe", new[] { "-d", WslDistro, "--", "bash", "-lc", script }, 5000);
            return Tuple.Create(result.Ok, result.Stdout, result.Stderr + result.Error);
        }

        Tuple<bool, object, string> GetChannelsStatusReadonly()
        {
            return RunOpenClawJson(new[] { "channels", "status", "--json", "--timeout", "8000" }, 10000);
        }

        Tuple<bool, string, string> GetAgentsBindingsReadonly()
        {
            return RunOpenClawText(new[] { "agents", "bindings" }, 10000);
        }

        Tuple<bool, object, string> GetSessionsActive24hReadonly()
        {
            return RunOpenClawJson(new[] { "sessions", "--all-agents", "--active", "1440", "--json" }, 8000);
        }

        Tuple<bool, object, string> GetTasksListReadonly()
        {
            return RunOpenClawJson(new[] { "tasks", "list", "--json" }, 8000);
        }

        Tuple<bool, string, string> GetEntrancePressureLogsReadonly()
        {
            var script =
                "journalctl --user -u openclaw-gateway.service --since '30 minutes ago' --no-pager -o short-iso 2>/dev/null | " +
                "grep -iE 'telegram|memory-core|dreaming|session file locked|cleanup timed out|agent cleanup timed out|fetch timeout|sync failed|liveness warning|event_loop|message\\.processed' | tail -120";
            var result = RunProcess("wsl.exe", new[] { "-d", WslDistro, "--", "bash", "-lc", script }, 5000);
            if (!result.Ok && string.IsNullOrWhiteSpace(result.Stdout)) return Tuple.Create(true, "", "");
            return Tuple.Create(result.Ok, result.Stdout, result.Stderr + result.Error);
        }

        void ShowDiagnosticsDialog(DiagnosticsSnapshot d)
        {
            var report = BuildDiagnosticsReport(d);
            using (var form = new Form())
            {
                form.Text = "OpenClaw 诊断 v0（只读）";
                form.StartPosition = FormStartPosition.CenterParent;
                form.Size = new Size(900, 680);
                form.BackColor = Color.White;
                form.Font = new Font("Microsoft YaHei UI", 9f);
                if (Icon != null) form.Icon = Icon;

                var title = MakeLabel("OpenClaw 诊断 v0（只读）", 18, 14, 420, 28, 13f, Color.FromArgb(15, 23, 42), true);
                var subtitle = MakeLabel("不启动、不重启、不改配置；报告已在进入窗口前脱敏。", 18, 42, 780, 24, 9f, Color.FromArgb(100, 116, 139), false);
                var box = new TextBox
                {
                    Multiline = true,
                    ReadOnly = true,
                    ScrollBars = ScrollBars.Both,
                    WordWrap = false,
                    Text = report,
                    Location = new Point(18, 74),
                    Size = new Size(844, 510),
                    Font = new Font("Consolas", 9f),
                    BackColor = Color.FromArgb(248, 250, 252),
                    ForeColor = Color.FromArgb(31, 41, 55)
                };
                var copy = new Button { Text = "复制脱敏报告", Location = new Point(18, 602), Size = new Size(130, 34), BackColor = Color.FromArgb(37, 99, 235), ForeColor = Color.White, FlatStyle = FlatStyle.Flat };
                copy.FlatAppearance.BorderSize = 0;
                copy.Click += (s, e) => { Clipboard.SetText(report); copy.Text = "已复制"; };
                var close = new Button { Text = "关闭", Location = new Point(760, 602), Size = new Size(102, 34), BackColor = Color.FromArgb(15, 23, 42), ForeColor = Color.White, FlatStyle = FlatStyle.Flat };
                close.FlatAppearance.BorderSize = 0;
                close.Click += (s, e) => form.Close();
                form.Controls.AddRange(new Control[] { title, subtitle, box, copy, close });
                form.ShowDialog(this);
            }
        }

        string BuildDiagnosticsReport(DiagnosticsSnapshot d)
        {
            var sb = new StringBuilder();
            sb.AppendLine("OpenClaw 诊断报告 v0");
            sb.AppendLine("时间：" + d.GeneratedAt.ToString("yyyy-MM-dd HH:mm:ss"));
            sb.AppendLine("整体：" + DiagnosticsStateLabel(d.OverallState));
            foreach (var reason in d.OverallReasons) sb.AppendLine("- " + RedactSensitive(reason));
            AppendDiagnosticsSection(sb, "Gateway", d.Gateway);
            AppendDiagnosticsSection(sb, "Gateway Resilience", d.GatewayResilience);
            AppendDiagnosticsSection(sb, "Network Stability", d.NetworkStability);
            AppendDiagnosticsSection(sb, "Entrance Pressure", d.EntrancePressure);
            AppendDiagnosticsSection(sb, "Telegram", d.Telegram);
            AppendDiagnosticsSection(sb, "Sessions", d.Sessions);
            AppendDiagnosticsSection(sb, "Tasks & Logs", d.TasksLogs);
            sb.AppendLine();
            sb.AppendLine("v0 边界：只读；不自动重启、不 maintenance --apply、不清理 session、不改 binding/model/secrets、不写 memory。");
            return RedactSensitive(sb.ToString());
        }

        Dictionary<string, string> ParseKeyValueLines(string text)
        {
            var result = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (var line in (text ?? "").Split(new[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries))
            {
                var idx = line.IndexOf('=');
                if (idx <= 0) continue;
                result[line.Substring(0, idx).Trim()] = line.Substring(idx + 1).Trim();
            }
            return result;
        }

        string GetValue(Dictionary<string, string> values, string key)
        {
            string value;
            return values != null && values.TryGetValue(key, out value) ? value : "";
        }

        void AppendDiagnosticsSection(StringBuilder sb, string title, List<DiagnosticItem> items)
        {
            sb.AppendLine();
            sb.AppendLine("[" + title + "]");
            if (items.Count == 0)
            {
                sb.AppendLine("- 未读取到数据");
                return;
            }
            foreach (var item in items)
            {
                sb.AppendLine("- " + item.Label + "：" + item.Value + " ｜" + DiagnosticsStateLabel(item.State) + "｜" + RedactSensitive(item.Reason) + (string.IsNullOrWhiteSpace(item.Source) ? "" : " ｜source=" + item.Source));
            }
        }

        string DiagnosticsStateLabel(string state)
        {
            if (state == "Good" || state == "Normal") return "正常";
            if (state == "Warn" || state == "Observe") return "需观察";
            if (state == "Risk" || state == "HighRisk") return "高风险";
            if (state == "Confirm") return "需要人工确认";
            return "未知";
        }

        static int CountMatches(string text, string pattern)
        {
            if (string.IsNullOrEmpty(text)) return 0;
            return Regex.Matches(text, pattern, RegexOptions.IgnoreCase).Count;
        }

        static string LastMatchingLine(string text, string pattern)
        {
            if (string.IsNullOrEmpty(text)) return "";
            var lines = text.Split(new[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries);
            for (var i = lines.Length - 1; i >= 0; i--)
            {
                if (Regex.IsMatch(lines[i], pattern, RegexOptions.IgnoreCase)) return lines[i];
            }
            return "";
        }

        string RedactSensitive(string text)
        {
            if (string.IsNullOrEmpty(text)) return text ?? "";
            var value = text;
            value = Regex.Replace(value, "(?i)(Authorization\\s*:\\s*Bearer\\s+)[^\\s'\\\";]+", "$1[REDACTED]");
            value = Regex.Replace(value, "(?i)(Bearer\\s+)[A-Za-z0-9._\\-+/=]{16,}", "$1[REDACTED]");
            value = Regex.Replace(value, "(?i)((api[_-]?key|botToken|token|secret|password|access_token|refresh_token|oauth)[\\\"'\\s:=]+)[^\\s,;\\\"']{8,}", "$1[REDACTED]");
            value = Regex.Replace(value, "(?i)(OPENAI_API_KEY|VOLCANO_ENGINE_API_KEY|TELEGRAM[^\\s=]*TOKEN)(\\s*=\\s*)[^\\s]+", "$1$2[REDACTED]");
            return value;
        }

        string FirstMeaningfulLine(string text, string skip)
        {
            foreach (var line in (text ?? "").Split(new[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries))
            {
                var trimmed = line.Trim();
                if (trimmed.Length == 0) continue;
                if (!string.IsNullOrEmpty(skip) && trimmed.IndexOf(skip, StringComparison.OrdinalIgnoreCase) >= 0) continue;
                return trimmed;
            }
            return "-";
        }

        async Task ToggleOpenClawAsync()
        {
            if (togglingOpenClaw) return;
            var serviceActive = await Task.Run(() => GatewayServiceLooksActive());
            var shouldStop = serviceActive || lastOpenClawServiceActive;
            lastOpenClawServiceActive = shouldStop;
            togglingOpenClaw = true;
            startupNote = shouldStop ? "正在关闭 OpenClaw..." : "正在启动 OpenClaw...";
            updated.Text = startupNote;
            UpdateOpenClawPowerUi();
            try
            {
                var result = await Task.Run(() => shouldStop ? StopOpenClawGateway() : StartOpenClawGateway());
                lastOpenClawServiceActive = result.Ok ? !shouldStop : GatewayServiceLooksActive();
                startupNote = result.Ok
                    ? (shouldStop ? "OpenClaw 已关闭。" : "OpenClaw 已启动，正在检查 Telegram。")
                    : (shouldStop ? "已尝试关闭 OpenClaw；如果仍显示运行，请稍后查看状态卡片或打开诊断。" : "已尝试启动 OpenClaw；如果仍异常，请查看状态卡片。");
            }
            finally
            {
                togglingOpenClaw = false;
                UpdateOpenClawPowerUi();
            }
            await RefreshAsync();
        }

        void UpdateOpenClawPowerUi()
        {
            if (openClawPowerButton == null) return;

            var text = togglingOpenClaw
                ? (lastOpenClawServiceActive ? "关闭中..." : "启动中...")
                : (lastOpenClawServiceActive ? "关闭 OpenClaw" : "开启 OpenClaw");

            openClawPowerButton.Text = text;
            openClawPowerButton.Enabled = !togglingOpenClaw;
            openClawPowerButton.BackColor = togglingOpenClaw
                ? Color.FromArgb(148, 163, 184)
                : lastOpenClawServiceActive ? Color.FromArgb(220, 38, 38) : Color.FromArgb(22, 163, 74);

            if (openClawPowerTrayItem != null)
            {
                openClawPowerTrayItem.Text = text;
                openClawPowerTrayItem.Enabled = !togglingOpenClaw;
            }
        }

        CommandResult StartOpenClawGateway()
        {
            var script = OpenClawBootstrapScript() +
                "systemctl --user start openclaw-gateway.service >/dev/null 2>&1 || true\n" +
                "pgrep -af 'openclaw-manual-keepalive' >/dev/null 2>&1 || (nohup bash -lc 'exec -a openclaw-manual-keepalive sleep infinity' >/dev/null 2>&1 &)\n" +
                "for i in $(seq 1 45); do \"$OPENCLAW_BIN\" gateway probe >/dev/null 2>&1 && exit 0; sleep 1; done\n" +
                "exit 1";
            return RunProcess("wsl.exe", new[] { "-d", WslDistro, "--", "bash", "-lc", script }, 60000);
        }

        CommandResult StopOpenClawGateway()
        {
            var script = OpenClawBootstrapScript() +
                "systemctl --user stop openclaw-gateway.service >/dev/null 2>&1 || true\n" +
                "pkill -f '[o]penclaw-manual-keepalive' >/dev/null 2>&1 || true\n" +
                "for i in $(seq 1 20); do \"$OPENCLAW_BIN\" gateway probe >/dev/null 2>&1 || exit 0; sleep 1; done\n" +
                "exit 1";
            return RunProcess("wsl.exe", new[] { "-d", WslDistro, "--", "bash", "-lc", script }, 30000);
        }

        async Task ToggleClashSafeModeAsync()
        {
            clashSafeModeEnabled = !clashSafeModeEnabled;
            SaveClashSafeModeEnabled(clashSafeModeEnabled);
            SyncClashSafeModeUi();
            await EnsureClashSafeModeAsync(true);
        }

        void SyncClashSafeModeUi()
        {
            if (clashSafeModeCheck != null && clashSafeModeCheck.Checked != clashSafeModeEnabled)
                clashSafeModeCheck.Checked = clashSafeModeEnabled;
            if (clashSafeModeTrayItem != null)
                clashSafeModeTrayItem.Checked = clashSafeModeEnabled;
        }

        async Task EnsureClashSafeModeAsync(bool manual)
        {
            if (enforcingClashMode) return;
            if (!clashSafeModeEnabled)
            {
                SyncClashSafeModeUi();
                return;
            }

            enforcingClashMode = true;
            try
            {
                await Task.Run(() => EnsureClashSafeMode());
            }
            catch
            {
            }
            finally
            {
                enforcingClashMode = false;
            }
        }

        string EnsureClashSafeMode()
        {
            var mode = GetClashMode();
            if (mode == "global")
            {
                InvokeMihomoPipe("PATCH", "/configs", "{\"mode\":\"rule\"}");
                mode = GetClashMode();
                return mode == "rule" ? "已切回规则模式" : "当前模式：" + mode;
            }
            if (mode == "rule") return "已开启：规则 + GLOBAL 节点";
            if (mode == "direct") return "当前直连，按需切规则";
            return "当前模式：" + mode;
        }

        string GetClashMode()
        {
            var response = InvokeMihomoPipe("GET", "/configs", "");
            var match = Regex.Match(response, "\\\"mode\\\":\\\"([^\\\"]+)\\\"");
            return match.Success ? match.Groups[1].Value : "unknown";
        }

        string InvokeMihomoPipe(string method, string path, string body)
        {
            using (var pipe = new NamedPipeClientStream(".", "verge-mihomo", PipeDirection.InOut))
            {
                pipe.Connect(1000);
                var writer = new StreamWriter(pipe, new UTF8Encoding(false));
                writer.AutoFlush = true;
                var reader = new StreamReader(pipe, Encoding.UTF8);
                var headers = new List<string>
                {
                    method + " " + path + " HTTP/1.1",
                    "Host: localhost",
                    "Connection: close"
                };
                var secret = ResolveMihomoSecret();
                if (!string.IsNullOrEmpty(secret))
                    headers.Insert(2, "Authorization: Bearer " + secret);
                if (!string.IsNullOrEmpty(body))
                {
                    headers.Add("Content-Type: application/json");
                    headers.Add("Content-Length: " + Encoding.UTF8.GetByteCount(body));
                }
                writer.Write(string.Join("\r\n", headers) + "\r\n\r\n" + body);
                return reader.ReadToEnd();
            }
        }

        string ResolveMihomoSecret()
        {
            var envSecret = Environment.GetEnvironmentVariable("CLASH_VERGE_SECRET");
            if (!string.IsNullOrEmpty(envSecret)) return envSecret.Trim();

            var appData = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
            var candidates = new[]
            {
                Path.Combine(appData, "io.github.clash-verge-rev.clash-verge-rev", "verge.yaml"),
                Path.Combine(appData, "io.github.clash-verge-rev.clash-verge", "verge.yaml"),
                Path.Combine(appData, "clash-verge-rev", "verge.yaml")
            };

            foreach (var candidate in candidates)
            {
                try
                {
                    if (!File.Exists(candidate)) continue;
                    var text = File.ReadAllText(candidate, Encoding.UTF8);
                    var match = Regex.Match(text, "^\\s*secret\\s*:\\s*[\"']?([^\"'\\r\\n#]+)", RegexOptions.Multiline);
                    if (match.Success) return match.Groups[1].Value.Trim();
                }
                catch
                {
                }
            }

            return "set-your-secret";
        }

        void OpenControl()
        {
            try
            {
                if (!lastGatewayOk)
                {
                    statusLine.Text = "OpenClaw 未启动。请先点击“开启 OpenClaw”。";
                    return;
                }

                var confirm = MessageBox.Show(
                    this,
                    "原生浏览器 Control 可能触发较重的会话/模型查询，打开后不要长时间挂着。\n\n确定现在打开吗？",
                    "打开原生 Control",
                    MessageBoxButtons.OKCancel,
                    MessageBoxIcon.Warning);
                if (confirm != DialogResult.OK) return;

                statusLine.Text = "正在打开浏览器版 Control...";
                var script = Path.Combine(Application.StartupPath, "Start-OpenClaw.ps1");
                if (File.Exists(script))
                {
                    Process.Start(new ProcessStartInfo
                    {
                        FileName = "powershell.exe",
                        Arguments = "-NoProfile -ExecutionPolicy Bypass -File " + QuoteArg(script) + " -OpenBrowser",
                        UseShellExecute = false,
                        CreateNoWindow = true
                    });
                    return;
                }
                Process.Start("http://127.0.0.1:18789/");
            }
            catch (Exception ex)
            {
                statusLine.Text = "打开 Control 失败：" + ex.Message;
            }
        }

        Snapshot BuildSnapshot()
        {
            var snapshot = new Snapshot();
            SetStartupProgress(snapshot, 100, "本地事实快照", "主面板只读 systemd、进程、端口、缓存和 task record。");
            FillLocalGatewayFacts(snapshot);
            FillTaskRecordsSnapshot(snapshot);
            FillAgentEvidenceSnapshot(snapshot);
            snapshot.CollabStatus = ReadAgentRoomCollabStatus();
            FinalizeMainPanelConnectivityState(snapshot);
            FillUsageCacheSnapshot(snapshot);
            FillReliabilitySnapshot(snapshot);

            if (snapshot.Tasks.Count == 0)
                snapshot.Tasks.Add(new[] { "本地 task record", "缓存", "无活动", "-", "未发现 running / cooling_down / awaiting_main_review 任务" });
            if (string.IsNullOrWhiteSpace(snapshot.StatusLine))
                snapshot.StatusLine = snapshot.OpenClawServiceActive
                    ? "本地服务快照已更新；主面板未调用 gateway RPC。"
                    : "未看到本地 OpenClaw gateway 服务运行。";
            if (!string.IsNullOrWhiteSpace(startupNote))
                snapshot.StatusLine = startupNote + " | " + snapshot.StatusLine;
            return snapshot;
        }

        void FillLocalGatewayFacts(Snapshot s)
        {
            var facts = ReadLocalGatewayFacts();
            s.OpenClawServiceActive = facts.ServiceActive || facts.ProcessRunning || facts.PortListening;
            s.GatewayOk = facts.PortListening && (facts.ServiceActive || facts.ProcessRunning);
            s.GatewaySoftFailure = s.OpenClawServiceActive && !s.GatewayOk;

            if (s.GatewayOk)
            {
                gatewayProbeFailures = 0;
                s.State = "Ready";
                s.GatewayText = string.IsNullOrWhiteSpace(facts.CpuPercent)
                    ? "在线"
                    : "CPU " + facts.CpuPercent + "%";
                s.StatusLine = "本地 gateway active，端口 18789 正在监听；网关卡片显示 gateway 进程 CPU，主面板未调用 gateway RPC。";
                return;
            }

            if (s.OpenClawServiceActive)
            {
                gatewayProbeFailures++;
                s.State = "Working";
                s.GatewayText = facts.PortListening ? "端口监听" : "本地运行";
                s.StatusLine = "控制中心只读到部分本地事实；这不等于 OpenClaw 不可用。";
                return;
            }

            if (!string.IsNullOrWhiteSpace(facts.Error))
            {
                s.State = "Working";
                s.GatewayText = "未确认";
                s.GatewaySoftFailure = true;
                s.StatusLine = "控制中心本地读取失败；不代表 OpenClaw 不可用：" + Trim(facts.Error, 100);
                return;
            }

            s.State = "Problem";
            s.GatewayText = "未发现";
            s.StatusLine = "未看到 openclaw-gateway.service active、gateway 进程或 18789 监听端口。";
        }

        LocalGatewayFacts ReadLocalGatewayFacts()
        {
            var facts = new LocalGatewayFacts();
            var errors = new List<string>();

            var serviceResult = RunProcess("wsl.exe", new[] { "-d", WslDistro, "--", "bash", "-lc", "systemctl --user is-active openclaw-gateway.service 2>/dev/null || true" }, 3000);
            if (serviceResult.Ok)
                facts.ServiceState = serviceResult.Stdout.Trim();
            else
                errors.Add(serviceResult.Stderr + serviceResult.Error);
            facts.ServiceActive = facts.ServiceState.Equals("active", StringComparison.OrdinalIgnoreCase);

            var listenResult = RunProcess("wsl.exe", new[] { "-d", WslDistro, "--", "bash", "-lc", "ss -ltnp 2>/dev/null | grep -m1 ':18789' || true" }, 3000);
            if (listenResult.Ok)
            {
                var listenLine = listenResult.Stdout.Trim();
                facts.PortListening = listenLine.Length > 0;
                var pidMatch = Regex.Match(listenLine, @"pid=(\d+)");
                if (pidMatch.Success)
                    facts.Pid = pidMatch.Groups[1].Value;
            }
            else
            {
                errors.Add(listenResult.Stderr + listenResult.Error);
            }

            if (string.IsNullOrWhiteSpace(facts.Pid))
            {
                var pidResult = RunProcess("wsl.exe", new[] { "-d", WslDistro, "--", "bash", "-lc", "pgrep -f 'openclaw.*/dist/index.js gateway --port 18789|openclaw-gateway' | head -1 || true" }, 3000);
                if (pidResult.Ok)
                    facts.Pid = pidResult.Stdout.Trim().Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries).FirstOrDefault() ?? "";
                else
                    errors.Add(pidResult.Stderr + pidResult.Error);
            }

            facts.ProcessRunning = Regex.IsMatch(facts.Pid ?? "", @"^\d+$");

            if (facts.ProcessRunning)
            {
                var psResult = RunProcess("wsl.exe", new[] { "-d", WslDistro, "--", "bash", "-lc", "ps -p " + facts.Pid + " -o pid=,ppid=,pcpu=,rss=,etime=,args= 2>/dev/null | head -1" }, 3000);
                if (psResult.Ok)
                {
                    var match = Regex.Match(psResult.Stdout.Trim(), @"^\s*(\d+)\s+(\d+)\s+([\d.]+)\s+(\d+)\s+(\S+)\s+(.+)$");
                    if (match.Success)
                    {
                        facts.Pid = match.Groups[1].Value;
                        facts.CpuPercent = match.Groups[3].Value;
                        double rssKb;
                        if (double.TryParse(match.Groups[4].Value, NumberStyles.Float, CultureInfo.InvariantCulture, out rssKb))
                            facts.RssMb = Math.Round(rssKb / 1024.0).ToString("0", CultureInfo.InvariantCulture);
                        facts.Uptime = match.Groups[5].Value;
                    }
                }
                else
                {
                    errors.Add(psResult.Stderr + psResult.Error);
                }
            }

            if (!facts.ServiceActive && !facts.ProcessRunning && !facts.PortListening && errors.Count > 0)
                facts.Error = RedactSensitive(string.Join(" ", errors));
            return facts;
        }

        void FillTelegramLocalSignal(Snapshot s)
        {
            var signal = ReadTelegramLocalSignal();
            if (!string.IsNullOrWhiteSpace(signal.Error))
            {
                s.TelegramText = "读取失败";
                s.TelegramCardState = "warn";
                s.Logs.Add("Telegram 本地观测读取失败：" + Trim(signal.Error, 100));
                return;
            }

            var okRecent = signal.LastOkAgeMs >= 0 && signal.LastOkAgeMs <= 30L * 60L * 1000L;
            var failRecent = signal.LastFailureAgeMs >= 0 && signal.LastFailureAgeMs <= 15L * 60L * 1000L;
            var failureNewer = failRecent && (signal.LastOkAgeMs < 0 || signal.LastFailureAgeMs < signal.LastOkAgeMs);

            if (okRecent && !failureNewer)
            {
                s.TelegramOk = true;
                s.TelegramText = "最近已回复";
                s.TelegramCardState = "good";
                s.RecentSessionAge = Age(signal.LastOkAgeMs);
                s.Tasks.Insert(0, new[] { "Telegram 回复", "本地日志", "已回复", Age(signal.LastOkAgeMs), "最近 sendMessage ok；未调用 gateway RPC" });
                return;
            }

            if (failureNewer)
            {
                s.TelegramOk = false;
                s.TelegramText = "最近失败";
                s.TelegramCardState = "warn";
                s.Tasks.Insert(0, new[] { "Telegram 回复", "本地日志", "需观察", Age(signal.LastFailureAgeMs), "最近 sendMessage/getMe 失败；看最近提醒" });
                return;
            }

            if (signal.LastOkAgeMs >= 0)
            {
                s.TelegramText = "上次" + Age(signal.LastOkAgeMs);
                s.TelegramCardState = "warn";
                s.Tasks.Insert(0, new[] { "Telegram 回复", "本地日志", "无近期记录", Age(signal.LastOkAgeMs), "上次 sendMessage ok；主面板不主动测试 Telegram" });
                return;
            }

            s.TelegramText = s.OpenClawServiceActive ? "无近期记录" : "-";
            s.TelegramCardState = s.OpenClawServiceActive ? "warn" : "bad";
        }

        TelegramLocalSignal ReadTelegramLocalSignal()
        {
            var signal = new TelegramLocalSignal();
            var script =
                "log=/tmp/openclaw/openclaw-$(date +%F).log\n" +
                "[ -r \"$log\" ] || exit 0\n" +
                "grep -iE 'telegram sendMessage ok|telegram sendMessage failed|getMe|sendChatAction failed' \"$log\" 2>/dev/null | tail -120";
            var result = RunProcess("wsl.exe", new[] { "-d", WslDistro, "--", "bash", "-lc", script }, 3000);
            if (!result.Ok && string.IsNullOrWhiteSpace(result.Stdout))
            {
                signal.Error = RedactSensitive(result.Stderr + result.Error);
                return signal;
            }

            foreach (var rawLine in (result.Stdout ?? "").Split(new[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries))
            {
                var line = RedactSensitive(rawLine);
                var ageMs = LogLineAgeMs(line);
                if (line.IndexOf("telegram sendMessage ok", StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    signal.LastOkAgeMs = ageMs;
                    signal.LastOkLine = line;
                }
                else if (line.IndexOf("telegram sendMessage failed", StringComparison.OrdinalIgnoreCase) >= 0
                    || line.IndexOf("sendChatAction failed", StringComparison.OrdinalIgnoreCase) >= 0
                    || line.IndexOf("fetch timeout", StringComparison.OrdinalIgnoreCase) >= 0
                    || line.IndexOf("getMe", StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    signal.LastFailureAgeMs = ageMs;
                    signal.LastFailureLine = line;
                }
            }
            return signal;
        }

        long LogLineAgeMs(string line)
        {
            var match = Regex.Match(line ?? "", "\"time\"\\s*:\\s*\"([^\"]+)\"");
            if (!match.Success) match = Regex.Match(line ?? "", "\"date\"\\s*:\\s*\"([^\"]+)\"");
            if (!match.Success) return -1;
            DateTimeOffset parsed;
            if (DateTimeOffset.TryParse(match.Groups[1].Value, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out parsed))
                return (long)Math.Max(0, (DateTimeOffset.Now - parsed).TotalMilliseconds);
            return -1;
        }

        void FillTaskRecordsSnapshot(Snapshot s)
        {
            var result = ReadTaskRecords();
            if (!result.Item1)
            {
                s.Tasks.Add(new[] { "本地 task record", "本地文件", "读取失败", "-", Trim(result.Item3, 100) });
                return;
            }

            var rows = AsList(result.Item2).Cast<object>().Select(item => AsDict(item)).ToList();
            var active = 0;
            foreach (var row in rows.Take(12))
            {
                var type = Convert.ToString(Get(row, "task_type") ?? Get(row, "task_id") ?? "任务");
                var displayStatus = Convert.ToString(Get(row, "display_status") ?? Get(row, "status") ?? "");
                var status = TaskRecordStatusLabel(displayStatus, Convert.ToString(Get(row, "error_kind") ?? ""));
                var updatedAt = Convert.ToString(Get(row, "updated_at") ?? "");
                var age = AgeSince(updatedAt);
                var summary = Convert.ToString(Get(row, "result_summary") ?? "");
                var next = Convert.ToString(Get(row, "next_action") ?? "");
                var error = Convert.ToString(Get(row, "error_kind") ?? "");
                var detail = !string.IsNullOrWhiteSpace(summary) ? summary : !string.IsNullOrWhiteSpace(error) ? error : next;
                if (IsActiveTaskRecord(displayStatus))
                    active++;
                s.Tasks.Add(new[] { Trim(type, 42), "task record", status, age, Trim(detail, 110) });
                if (s.LastSessionAgeMs < 0)
                {
                    var ageMs = AgeSinceMs(updatedAt);
                    if (ageMs >= 0)
                    {
                        s.LastSessionAgeMs = ageMs;
                        s.RecentSessionAge = Age(ageMs);
                    }
                }
            }
            s.RunningTasks = active;
        }

        Tuple<bool, object, string> ReadTaskRecords()
        {
            var script = "cd ~/.openclaw/workspace 2>/dev/null && python3 scripts/task_record.py list --limit 12";
            var result = RunProcess("wsl.exe", new[] { "-d", WslDistro, "--", "bash", "-lc", script }, 5000);
            if (!result.Ok) return Tuple.Create(false, (object)null, RedactSensitive(result.Stderr + result.Error));
            try
            {
                return Tuple.Create(true, json.DeserializeObject(ExtractJsonValue(result.Stdout)), "");
            }
            catch (Exception ex)
            {
                return Tuple.Create(false, (object)null, "task record JSON 解析失败：" + ex.Message);
            }
        }

        void FillAgentEvidenceSnapshot(Snapshot s)
        {
            var config = ReadOpenClawConfig();
            if (!config.Item1)
            {
                s.Sessions.Add("Agent 活动读取失败：" + Trim(config.Item3, 90));
                return;
            }

            var root = AsDict(config.Item2);
            var agentsRoot = AsDict(Get(root, "agents"));
            var agents = AsList(Get(agentsRoot, "list"));
            if (agents.Count == 0)
            {
                s.Sessions.Add("未发现 agents.list。");
                return;
            }

            var lines = new List<Tuple<long, string>>();
            foreach (var item in agents.Cast<object>().Take(12))
            {
                var row = AsDict(item);
                var id = Convert.ToString(Get(row, "id") ?? "");
                if (string.IsNullOrWhiteSpace(id)) continue;
                var model = AgentModelLabel(Get(row, "model"));
                var prefix = id + (ToBool(Get(row, "default")) ? " · default" : "") + " · " + Trim(model, 34);
                var evidence = ReadLatestAgentSessionEvidence(id);
                if (!evidence.Item1)
                {
                    lines.Add(Tuple.Create(-1L, Trim(prefix + " · 无活动证据", 120)));
                    continue;
                }

                var session = AsDict(evidence.Item2);
                var status = Convert.ToString(Get(session, "status") ?? "");
                var updatedMs = LatestSessionTimestampMs(session);
                var age = updatedMs > 0 ? AgeSince(updatedMs) : "-";
                var sessionModel = Convert.ToString(Get(session, "model") ?? "");
                var statusLabel = AgentSessionStatusLabel(status);
                var bits = prefix + " · " + statusLabel + (age != "-" ? " · " + age + "前" : "");
                if (!string.IsNullOrWhiteSpace(sessionModel) && !model.Contains(sessionModel))
                    bits += " · session模型 " + Trim(sessionModel, 24);
                lines.Add(Tuple.Create(updatedMs, Trim(bits, 120)));
            }
            foreach (var line in lines.OrderByDescending(item => item.Item1))
                s.Sessions.Add(line.Item2);
        }

        Tuple<bool, object, string> ReadOpenClawConfig()
        {
            var result = RunProcess("wsl.exe", new[] { "-d", WslDistro, "--", "bash", "-lc", "cat ~/.openclaw/openclaw.json 2>/dev/null" }, 4000);
            if (!result.Ok || string.IsNullOrWhiteSpace(result.Stdout))
                return Tuple.Create(false, (object)null, RedactSensitive(result.Stderr + result.Error));
            try
            {
                return Tuple.Create(true, json.DeserializeObject(ExtractJsonObject(result.Stdout)), "");
            }
            catch (Exception ex)
            {
                return Tuple.Create(false, (object)null, "openclaw.json JSON 解析失败：" + ex.Message);
            }
        }

        Tuple<bool, object, string> ReadLatestAgentSessionEvidence(string agentId)
        {
            if (!Regex.IsMatch(agentId ?? "", @"^[A-Za-z0-9_.-]+$"))
                return Tuple.Create(false, (object)null, "");
            var script = "cat ~/.openclaw/agents/" + ShellQuote(agentId) + "/sessions/sessions.json 2>/dev/null || true";
            var result = RunProcess("wsl.exe", new[] { "-d", WslDistro, "--", "bash", "-lc", script }, 3000);
            if (!result.Ok || string.IsNullOrWhiteSpace(result.Stdout))
                return Tuple.Create(false, (object)null, "");
            try
            {
                var parsed = json.DeserializeObject(ExtractJsonObject(result.Stdout));
                var candidates = new List<Tuple<string, Dictionary<string, object>>>();
                var dict = AsDict(parsed);
                foreach (var entry in dict)
                {
                    var session = AsDict(entry.Value);
                    if (session.Count > 0)
                        candidates.Add(Tuple.Create(Convert.ToString(entry.Key) ?? "", session));
                }
                if (candidates.Count == 0)
                {
                    foreach (var item in AsList(parsed).Cast<object>())
                    {
                        var session = AsDict(item);
                        if (session.Count > 0)
                            candidates.Add(Tuple.Create(Convert.ToString(Get(session, "sessionKey") ?? Get(session, "id") ?? "") ?? "", session));
                    }
                }
                if (candidates.Count == 0)
                    return Tuple.Create(false, (object)null, "");
                var latest = candidates.OrderByDescending(c => LatestSessionTimestampMs(c.Item2)).First();
                return Tuple.Create(true, (object)latest.Item2, latest.Item1);
            }
            catch
            {
                return Tuple.Create(false, (object)null, "");
            }
        }

        string ReadAgentRoomCollabStatus()
        {
            var result = RunProcess("wsl.exe", new[] {
                "-d", WslDistro, "--", "bash", "-lc",
                "cat ~/.openclaw/workspace/codex-main-bridge/agent-room/collaboration-status/compact.txt 2>/dev/null || echo ''"
            }, 4000);
            if (result.Ok && !string.IsNullOrWhiteSpace(result.Stdout))
            {
                var lines = result.Stdout.Trim().Split('\n');
                var firstTwo = lines.Take(2).Select(l => l.Trim()).ToList();
                return string.Join(" | ", firstTwo);
            }
            return "";
        }

        string AgentModelLabel(object modelObj)
        {
            var model = AsDict(modelObj);
            if (model.Count > 0)
                return Convert.ToString(Get(model, "primary") ?? Get(model, "id") ?? "-") ?? "-";
            var text = Convert.ToString(modelObj ?? "");
            return string.IsNullOrWhiteSpace(text) ? "-" : text;
        }

        string AgentSessionStatusLabel(string status)
        {
            var key = (status ?? "").Trim().ToLowerInvariant();
            if (key == "running") return "运行中";
            if (key == "timeout") return "超时";
            if (key == "failed" || key == "error") return "失败";
            if (key == "done" || key == "completed" || key == "complete") return "已完成";
            if (string.IsNullOrWhiteSpace(status)) return "有活动记录";
            return status;
        }

        static long LatestSessionTimestampMs(Dictionary<string, object> session)
        {
            foreach (var key in new[] { "updatedAt", "lastEventAt", "lastActiveAt", "createdAt" })
            {
                var raw = Get(session, key);
                var number = ToLong(raw);
                if (number > 0)
                    return number < 10000000000L ? number * 1000L : number;
                var text = Convert.ToString(raw ?? "");
                if (!string.IsNullOrWhiteSpace(text))
                {
                    DateTimeOffset parsed;
                    if (DateTimeOffset.TryParse(text, CultureInfo.InvariantCulture, DateTimeStyles.AssumeLocal, out parsed))
                        return parsed.ToUniversalTime().ToUnixTimeMilliseconds();
                }
            }
            return -1;
        }

        static bool IsActiveTaskRecord(string status)
        {
            var key = (status ?? "").Trim().ToLowerInvariant();
            return key == "running" || key == "queued" || key == "pending" || key == "cooling_down" || key == "awaiting_main_review" || key == "main_review";
        }

        string TaskRecordStatusLabel(string status, string errorKind)
        {
            var key = (status ?? "").Trim().ToLowerInvariant();
            if (key == "awaiting_main_review" || key == "main_review") return "待主脑审核";
            if (key == "cooling_down") return "冷却中";
            if (key == "running") return string.IsNullOrWhiteSpace(errorKind) ? "运行中" : "状态未收敛";
            if (key == "failed") return "失败";
            if (key == "done" || key == "completed" || key == "complete") return "已完成";
            if (key == "queued" || key == "pending") return "等待中";
            return string.IsNullOrWhiteSpace(status) ? "-" : status;
        }

        bool ShouldUseStartupLightProbe(Snapshot snapshot)
        {
            if (!snapshot.GatewayOk) return !snapshot.OpenClawServiceActive;
            if (!snapshot.TelegramOk) return snapshot.StartupProgress > 0 && snapshot.StartupProgress < 100;
            return snapshot.StartupProgress > 0 && snapshot.StartupProgress < 100;
        }

        void FillStartupLightPlaceholders(Snapshot snapshot)
        {
            if (snapshot.GatewayOk)
            {
                snapshot.Tasks.Add(new[] { "\u8f7b\u91cf\u68c0\u67e5", "\u8f7b\u91cf\u63a2\u6d4b", "\u9700\u89c2\u5bdf", "-", "\u6682\u4e0d\u8bfb\u53d6\u91cd\u4efb\u52a1\uff0c\u907f\u514d\u62a2\u5360 gateway / Telegram \u5165\u53e3" });
                snapshot.Sessions.Add("\u542f\u52a8\u9636\u6bb5\u6682\u4e0d\u8bfb\u53d6\u4f1a\u8bdd\uff0c\u907f\u514d\u62d6\u6162 OpenClaw\u3002");
                snapshot.Logs.Add("\u542f\u52a8\u9636\u6bb5\u53ea\u68c0\u67e5\u7f51\u5173\u548c Telegram\uff1b\u5c31\u7eea\u540e\u518d\u52a0\u8f7d\u65e5\u5fd7\u3001\u4efb\u52a1\u3001Token \u548c\u6210\u672c\u3002");
            }
            else
            {
                snapshot.Tasks.Add(new[] { "\u7f51\u5173\u63a2\u6d4b", "\u8f7b\u91cf\u63a2\u6d4b", "\u672a\u8fde\u901a", "-", "OpenClaw gateway \u5c1a\u672a\u54cd\u5e94" });
                snapshot.Logs.Add("OpenClaw \u5c1a\u672a\u5b8c\u6210\u7f51\u5173\u63a2\u6d4b\u3002");
            }
        }

        void FillSteadyLightPlaceholders(Snapshot snapshot)
        {
            snapshot.Tasks.Add(new[] { "\u63a7\u5236\u4e2d\u5fc3\u81ea\u52a8\u5237\u65b0", "\u8f7b\u91cf\u63a2\u6d4b", "\u5df2\u5173\u95ed", "-", "\u4e3b\u9762\u677f\u4e0d\u505a\u5b9a\u65f6 gateway RPC\uff1b\u542f\u52a8\u6216\u660e\u786e\u64cd\u4f5c\u540e\u5237\u65b0\uff0c\u9700\u8981\u6df1\u67e5\u65f6\u70b9\u201c\u8bca\u65ad\u201d" });
            snapshot.Sessions.Add("\u81ea\u52a8\u5237\u65b0\u5df2\u964d\u7ea7\uff1a\u4e0d\u8bfb\u53d6 24h sessions / high-token \u5217\u8868\u3002\u9700\u8981\u65f6\u70b9\u201c\u8bca\u65ad\u201d\u3002");
            snapshot.Logs.Add("\u81ea\u52a8\u5237\u65b0\u5df2\u964d\u7ea7\uff1a\u4e0d\u8bfb\u53d6 logs.tail / tasks audit / TaskFlow / \u6210\u672c\u626b\u63cf\u3002");
            snapshot.TokenFlows.Add("\u81ea\u52a8\u5237\u65b0\u4e0d\u8bfb\u53d6 Token/\u6210\u672c\u5feb\u7167\uff0c\u907f\u514d\u89e6\u53d1\u91cd RPC\u3002\u9700\u8981\u7ec6\u8282\u65f6\u70b9\u201c\u8bca\u65ad\u201d\u3002");
            snapshot.CostText = "\u5df2\u8df3\u8fc7";
            snapshot.CostState = "warn";
        }

        bool GatewayServiceLooksActive()
        {
            var result = RunProcess("wsl.exe", new[] { "-d", WslDistro, "--", "bash", "-lc", "systemctl --user is-active openclaw-gateway.service 2>/dev/null" }, 8000);
            return result.Ok && result.Stdout.Trim().Equals("active", StringComparison.OrdinalIgnoreCase);
        }

        bool StatusShowsGatewayServiceRunning(object statusObj)
        {
            var status = AsDict(statusObj);
            var service = AsDict(Get(status, "gatewayService"));
            var runtime = AsDict(Get(service, "runtime"));
            var statusText = Convert.ToString(Get(runtime, "status")) ?? "";
            var stateText = Convert.ToString(Get(runtime, "state")) ?? "";
            return statusText.Equals("running", StringComparison.OrdinalIgnoreCase)
                || stateText.Equals("active", StringComparison.OrdinalIgnoreCase);
        }

        void SetStartupProgress(Snapshot s, int percent, string step, string detail)
        {
            s.StartupProgress = Math.Max(0, Math.Min(100, percent));
            s.StartupStep = string.IsNullOrWhiteSpace(step) ? "启动中" : step;
            s.StartupProgressText = string.IsNullOrWhiteSpace(detail) ? s.StartupStep : detail;
        }

        void FillFromProbe(Snapshot s, object probeObj)
        {
            var probe = AsDict(probeObj);
            s.GatewayOk = ToBool(Get(probe, "ok"));
            var target = First(AsList(Get(probe, "targets")));
            var targetDict = AsDict(target);
            var connect = AsDict(Get(targetDict, "connect"));
            var health = AsDict(Get(targetDict, "health"));
            var channels = AsDict(Get(health, "channels"));
            var telegramChannel = AsDict(Get(channels, "telegram"));
            var network = AsDict(Get(probe, "network"));

            var rpcOk = ToBool(Get(connect, "rpcOk"));
            var latency = ToLong(Get(connect, "latencyMs"));
            s.GatewayOk = s.GatewayOk && rpcOk;
            s.GatewayText = s.GatewayOk
                ? "可连接 " + (latency >= 0 ? latency + "毫秒" : "")
                : "需检查";
            SetStartupProgress(s, s.GatewayOk ? 55 : 35, s.GatewayOk ? "网关已响应" : "网关检查中", s.GatewayOk ? "gateway RPC 已响应，正在检查通道。" : "gateway 仍未稳定响应。");

            var tgConfigured = ToBool(Get(telegramChannel, "configured"));
            var tgRunning = ToBool(Get(telegramChannel, "running"));
            var tgConnected = ToBool(Get(telegramChannel, "connected"));
            s.TelegramOk = tgConfigured && tgRunning && tgConnected;
            s.TelegramText = !tgConfigured ? "未配置" : (s.TelegramOk ? "已连接" : "需检查");
            s.TelegramCardState = s.TelegramOk ? "good" : "bad";

            var summary = AsDict(Get(targetDict, "summary"));
            var summaryTasks = AsDict(Get(summary, "tasks"));
            if (summaryTasks.Count > 0)
                s.RunningTasks = Math.Max(s.RunningTasks, (int)Math.Max(0, ToLong(Get(summaryTasks, "active"))));

            var sessions = AsDict(Get(health, "sessions"));
            var recent = AsList(Get(sessions, "recent"));
            foreach (var item in recent.Cast<object>().Take(8))
            {
                var row = AsDict(item);
                var age = ToLong(Get(row, "age"));
                var key = Convert.ToString(Get(row, "key") ?? "");
                s.Sessions.Add(Pad(Age(age), 7) + " " + Trim(key, 70));
            }
            if (recent.Count > 0)
            {
                var age = ToLong(Get(AsDict(recent[0]), "age"));
                s.RecentSessionAge = Age(age);
                s.LastSessionAgeMs = age;
                s.LastSessionSource = TokenSource(Convert.ToString(Get(AsDict(recent[0]), "key") ?? ""));
                s.LastSessionModel = Convert.ToString(Get(AsDict(recent[0]), "model") ?? "-");
            }

            var url = Convert.ToString(Get(network, "localLoopbackUrl") ?? "ws://127.0.0.1:18789");
            s.StatusLine = url + " | Telegram " + s.TelegramText;
            if (!s.GatewayOk || (tgConfigured && !s.TelegramOk)) s.State = "Problem";
            if (s.GatewayOk && s.TelegramOk && s.State == "Idle") s.State = "Ready";
        }

        void FillFromGatewayStatus(Snapshot s, string statusText)
        {
            var text = RedactSensitive(statusText ?? "");
            var running = Regex.IsMatch(text, @"Runtime:\s+running", RegexOptions.IgnoreCase);
            var connectivityOk = Regex.IsMatch(text, @"Connectivity probe:\s+ok", RegexOptions.IgnoreCase);
            var admin = Regex.IsMatch(text, @"Capability:\s+admin-capable", RegexOptions.IgnoreCase);
            var listening = Regex.Match(text, @"Listening:\s*([^\r\n]+)", RegexOptions.IgnoreCase);
            var target = listening.Success ? listening.Groups[1].Value.Trim() : "127.0.0.1:18789";

            s.OpenClawServiceActive = running || s.OpenClawServiceActive;
            s.GatewayOk = running && connectivityOk;
            s.GatewaySoftFailure = running && !s.GatewayOk;
            s.GatewayText = s.GatewayOk ? "本地可用" : running ? "服务运行" : "需检查";

            if (s.GatewayOk)
            {
                SetStartupProgress(s, 55, "本地网关可用", "gateway status 显示本地 connectivity probe 正常，正在检查 Telegram 通道。");
                s.StatusLine = target + " | 本地 gateway 正常" + (admin ? " | admin-capable" : "");
            }
            else if (running)
            {
                s.State = "Working";
                SetStartupProgress(s, 100, "本地服务运行", "OpenClaw gateway 进程仍在；本轮 connectivity probe 未在超时内完成。");
                s.StatusLine = "OpenClaw gateway 进程仍在；本轮轻探测超时，不等于服务不可用。";
            }
            else
            {
                s.State = "Problem";
                SetStartupProgress(s, 0, "网关未运行", "未看到 Runtime: running。");
                s.StatusLine = "OpenClaw gateway 未运行或状态不可读。";
            }
        }

        void FinalizeMainPanelConnectivityState(Snapshot s)
        {
            if (s.GatewayOk && s.State == "Idle")
                s.State = "Ready";
        }

        void FillChannelStatus(Snapshot s, object channelStatusObj)
        {
            var data = AsDict(channelStatusObj);
            if (data.Count == 0) return;

            var accountsByChannel = AsDict(Get(data, "channelAccounts"));
            var telegramAccounts = AsList(Get(accountsByChannel, "telegram"));
            var account = telegramAccounts.Count > 0 ? AsDict(telegramAccounts[0]) : new Dictionary<string, object>();
            var channels = AsDict(Get(data, "channels"));
            var telegram = AsDict(Get(channels, "telegram"));

            var source = account.Count > 0 ? account : telegram;
            if (source.Count == 0) return;

            var configured = ToBool(Get(source, "configured"));
            var running = ToBool(Get(source, "running"));
            var connected = ToBool(Get(source, "connected"));
            s.TelegramLastStartAt = ToLong(Get(source, "lastStartAt"));
            s.TelegramLastInboundAt = ToLong(Get(source, "lastInboundAt"));
            s.TelegramLastOutboundAt = ToLong(Get(source, "lastOutboundAt"));
            var eventLoop = AsDict(Get(data, "eventLoop"));
            var eventLoopDegraded = ToBool(Get(eventLoop, "degraded"));

            var startAgeMs = MillisecondsSince(s.TelegramLastStartAt);
            var startupWindow = startAgeMs <= 120000;

            if (!configured)
            {
                s.TelegramOk = false;
                s.TelegramText = "未配置";
                s.TelegramCardState = "bad";
                SetStartupProgress(s, 60, "Telegram 未配置", "gateway 已响应，但 Telegram 通道没有可用配置。");
                return;
            }

            if (!running)
            {
                s.TelegramOk = false;
                s.TelegramText = "需检查";
                s.TelegramCardState = startupWindow ? "warn" : "bad";
                SetStartupProgress(s, startupWindow ? 65 : 60, "Telegram 启动中", startupWindow ? "Telegram 通道正在启动，已等待 " + AgeSince(s.TelegramLastStartAt) + "。" : "Telegram 通道未运行。");
                if (startupWindow && s.State == "Problem") s.State = "Degraded";
                return;
            }

            if (!connected)
            {
                s.TelegramOk = false;
                s.TelegramText = "需检查";
                s.TelegramCardState = startupWindow ? "warn" : "bad";
                SetStartupProgress(s, startupWindow ? 78 : 70, "Telegram 连接中", startupWindow ? "Telegram polling 正在连接，已等待 " + AgeSince(s.TelegramLastStartAt) + "。" : "Telegram 通道运行中，但尚未连接。");
                if (startupWindow && s.State == "Problem") s.State = "Degraded";
                return;
            }

            var inboundSeen = s.TelegramLastInboundAt > 0 && (s.TelegramLastStartAt <= 0 || s.TelegramLastInboundAt >= s.TelegramLastStartAt);
            var outboundSeen = s.TelegramLastOutboundAt > 0 && (s.TelegramLastStartAt <= 0 || s.TelegramLastOutboundAt >= s.TelegramLastStartAt);
            s.TelegramOk = true;
            s.TelegramText = outboundSeen ? "已回复" : inboundSeen ? "已收未回证" : "已连接未验证";
            s.TelegramCardState = outboundSeen && !eventLoopDegraded ? "good" : "warn";

            if (startupWindow)
            {
                var warmupProgress = 85 + (int)Math.Min(14, Math.Max(0, startAgeMs / 9000));
                SetStartupProgress(s, warmupProgress, "冷启动预热", "Telegram 已连接，等待模型、sidecar 和通道稳定：" + AgeSince(s.TelegramLastStartAt) + "。");
                if (s.State == "Problem") s.State = "Degraded";
                if (string.IsNullOrWhiteSpace(s.StatusLine) || s.StatusLine.Contains("Telegram 已连接"))
                    s.StatusLine = "Telegram 已连接；OpenClaw 刚启动 " + AgeSince(s.TelegramLastStartAt) + "，模型和 sidecar 可能仍在预热。";
                return;
            }

            SetStartupProgress(s, 100, outboundSeen ? "回复链路已验证" : inboundSeen ? "已收到入站" : "连接已就绪", outboundSeen ? "本次启动后已有 Telegram 回复记录。" : inboundSeen ? "本次启动后已收到 Telegram 入站；尚未看到本地 outbound 记录。" : "gateway 和 Telegram 已稳定，冷启动窗口已结束；尚未看到本地入站/回复记录。");
            if (eventLoopDegraded)
            {
                var reasons = string.Join(",", AsList(Get(eventLoop, "reasons")).Cast<object>().Select(x => Convert.ToString(x)));
                s.StatusLine = string.IsNullOrWhiteSpace(s.StatusLine)
                    ? "Telegram 在线但入口压力偏高：" + reasons
                    : s.StatusLine + " | 入口压力偏高：" + reasons;
            }
        }

        void FillTasks(Snapshot s, object tasksObj)
        {
            var data = AsDict(tasksObj);
            var items = AsList(Get(data, "tasks"));
            var activeItems = items.Cast<object>()
                .Select(item => AsDict(item))
                .Where(row =>
                {
                    var status = Convert.ToString(Get(row, "status") ?? "").ToLowerInvariant();
                    return status == "running" || status == "queued";
                })
                .ToList();

            s.RunningTasks = activeItems.Count;
            foreach (var row in activeItems.Take(20))
            {
                var label = Convert.ToString(Get(row, "label") ?? Get(row, "taskId") ?? "任务");
                var runtime = Convert.ToString(Get(row, "runtime") ?? "-");
                var rawStatus = Convert.ToString(Get(row, "status") ?? "-");
                var status = TranslateTaskStatus(rawStatus);
                var created = AgeSince(ToLong(Get(row, "createdAt")));
                var lastEventAt = ToLong(Get(row, "lastEventAt"));
                var last = AgeSince(lastEventAt);
                var lastAgeMs = MillisecondsSince(lastEventAt);
                if (lastAgeMs <= freshTaskEventWindowMs)
                    last += " · 活跃";
                else if (lastAgeMs > activeTaskEventWindowMs)
                {
                    status += "（静默）";
                    last += " · 事件偏旧";
                }
                s.Tasks.Add(new[] { Trim(label, 42), runtime, status, created, last });
            }
        }

        void FillFlows(Snapshot s, Tuple<bool, string, string> flowData)
        {
            if (flowData == null || !flowData.Item1 || string.IsNullOrWhiteSpace(flowData.Item2)) return;
            var match = System.Text.RegularExpressions.Regex.Match(
                flowData.Item2,
                @"TaskFlow pressure:\s*(\d+)\s+active\s+.\s+(\d+)\s+blocked\s+.\s+(\d+)\s+cancel-requested");
            if (!match.Success) return;

            s.FlowActive = (int)ToLong(match.Groups[1].Value);
            s.FlowBlocked = (int)ToLong(match.Groups[2].Value);
            s.FlowCancelRequested = (int)ToLong(match.Groups[3].Value);

            if (s.FlowActive > 0 || s.FlowBlocked > 0 || s.FlowCancelRequested > 0)
            {
                var status = s.FlowActive > 0 ? "运行中" : s.FlowBlocked > 0 ? "阻塞" : "取消中";
                var last = "active " + s.FlowActive + " / blocked " + s.FlowBlocked + " / cancel " + s.FlowCancelRequested;
                s.Tasks.Add(new[] { "TaskFlow 后台流程", "flow", status, "-", last });
            }
        }

        void FillTokenUsage(Snapshot s, object statusObj)
        {
            var status = AsDict(statusObj);
            var sessionsRoot = AsDict(Get(status, "sessions"));
            var recent = AsList(Get(sessionsRoot, "recent"));
            foreach (var item in recent.Cast<object>().Take(12))
            {
                var row = AsDict(item);
                var input = Math.Max(0, ToLong(Get(row, "inputTokens")));
                var output = Math.Max(0, ToLong(Get(row, "outputTokens")));
                var cacheRead = Math.Max(0, ToLong(Get(row, "cacheRead")));
                var cacheWrite = Math.Max(0, ToLong(Get(row, "cacheWrite")));
                var total = Math.Max(0, ToLong(Get(row, "totalTokens")));
                var context = Math.Max(0, ToLong(Get(row, "contextTokens")));
                var percent = ToLong(Get(row, "percentUsed"));
                var key = Convert.ToString(Get(row, "key") ?? "");
                var model = Convert.ToString(Get(row, "model") ?? "-");
                var age = ToLong(Get(row, "age"));
                var source = TokenSource(key);

                if (s.LastSessionAgeMs < 0 || (age >= 0 && age < s.LastSessionAgeMs))
                {
                    s.LastSessionAgeMs = age;
                    s.LastSessionSource = source;
                    s.LastSessionModel = model;
                    s.RecentSessionAge = Age(age);
                }

                s.TokenInput += input;
                s.TokenOutput += output;
                s.TokenCacheRead += cacheRead;
                s.TokenTotal += total;

                if (s.TokenContext == "-" && total > 0 && context > 0)
                    s.TokenContext = FormatTokens(total) + " / " + FormatTokens(context) + (percent >= 0 ? " (" + percent + "%)" : "");

                var bits = source + " · " + model + " · " + Age(age);
                var usage = "总 " + FormatTokens(total) + "｜入 " + FormatTokens(input) + "｜出 " + FormatTokens(output) + "｜缓存 " + FormatTokens(cacheRead + cacheWrite);
                s.TokenFlows.Add(bits + "    " + usage);
            }

            if (s.TokenFlows.Count == 0)
                s.TokenFlows.Add("暂时没有可用的 Token 会话快照。");
        }

        void FillUsageCacheSnapshot(Snapshot s)
        {
            var cache = ReadUsageCacheSummary();
            if (!cache.Available)
            {
                s.TokenFlows.Add(string.IsNullOrWhiteSpace(cache.Error)
                    ? "Token/成本 · 暂无离线缓存；控制中心没有向 gateway 发起重查询。"
                    : "Token/成本 · 缓存读取失败：" + Trim(cache.Error, 100));
                s.CostText = "暂无缓存";
                s.CostState = "warn";
                return;
            }

            s.UsageCacheVisible = true;
            s.UsageCacheStale = cache.Stale;
            s.UsageCacheAge = cache.AgeMs >= 0 ? Age(cache.AgeMs) : "-";
            s.TokenInput = cache.InputTokens;
            s.TokenOutput = cache.OutputTokens;
            s.TokenCacheRead = cache.CacheReadTokens;
            s.TokenTotal = cache.TotalTokens > 0 ? cache.TotalTokens : cache.InputTokens + cache.OutputTokens;
            if (cache.SessionTotalTokens > 0 && cache.SessionContextTokens > 0)
            {
                var percent = cache.SessionContextLimit > 0
                    ? (int)Math.Round(cache.SessionContextTokens * 100.0 / Math.Max(1, cache.SessionContextLimit))
                    : -1;
                s.TokenContext = FormatTokens(cache.SessionTotalTokens) + " / " + FormatTokens(cache.SessionContextTokens) + (percent >= 0 ? " (" + percent + "%)" : "");
            }
            s.CostText = cache.HasEstimatedCost ? FormatUsd(cache.EstimatedCost) : "未记录";
            s.CostState = cache.Stale ? "warn" : cache.HasEstimatedCost && cache.EstimatedCost > 0 ? "work" : "good";

            s.TokenFlows.Clear();
            s.TokenFlows.Add("缓存 · " + (cache.Stale ? "数据较旧" : "已更新") + " · " + (cache.AgeMs >= 0 ? Age(cache.AgeMs) + "前" : cache.GeneratedAt) + " · 控制中心未查询 gateway");
            if (cache.HasEstimatedCost)
                s.TokenFlows.Add("成本 · " + (string.IsNullOrWhiteSpace(cache.CostPeriod) ? "本自然月" : cache.CostPeriod) + "累计 · " + FormatUsd(cache.EstimatedCost));
            foreach (var line in cache.Lines.Take(8))
                s.TokenFlows.Add(line);

            if (cache.Stale && !string.IsNullOrWhiteSpace(s.StatusLine))
                s.StatusLine += " | Token/成本缓存较旧";
            else if (cache.Stale)
                s.StatusLine = "Token/成本缓存较旧；控制中心仍保持只读缓存模式。";
        }

        UsageCacheSummary ReadUsageCacheSummary()
        {
            var summary = new UsageCacheSummary();
            try
            {
                var script = "cat ~/.openclaw/monitor-cache/usage-summary.json 2>/dev/null";
                var result = RunProcess("wsl.exe", new[] { "-d", WslDistro, "--", "bash", "-lc", script }, 3000);
                if (!result.Ok || string.IsNullOrWhiteSpace(result.Stdout))
                {
                    summary.Error = "未找到 ~/.openclaw/monitor-cache/usage-summary.json";
                    return summary;
                }

                var payload = AsDict(json.DeserializeObject(ExtractJsonObject(result.Stdout)));
                summary.Status = Convert.ToString(Get(payload, "status") ?? "");
                summary.GeneratedAt = Convert.ToString(Get(payload, "generatedAt") ?? "");
                summary.Stale = ToBool(Get(payload, "stale"));
                summary.AgeMs = UsageCacheAgeMs(summary.GeneratedAt);
                if (summary.AgeMs > 15L * 60L * 1000L) summary.Stale = true;

                var today = AsDict(Get(payload, "today"));
                summary.InputTokens = Math.Max(0, ToLong(Get(today, "inputTokens")));
                summary.OutputTokens = Math.Max(0, ToLong(Get(today, "outputTokens")));
                summary.TotalTokens = Math.Max(0, ToLong(Get(today, "totalTokens")));
                summary.CacheReadTokens = Math.Max(0, ToLong(Get(today, "cacheReadTokens")));
                summary.CacheWriteTokens = Math.Max(0, ToLong(Get(today, "cacheWriteTokens")));
                var month = AsDict(Get(payload, "currentMonth"));
                var monthCostObj = Get(month, "estimatedCost");
                var todayCostObj = Get(today, "estimatedCost");
                var costObj = monthCostObj ?? todayCostObj;
                summary.EstimatedCost = ToDouble(costObj);
                summary.HasEstimatedCost = costObj != null && summary.EstimatedCost > 0;
                summary.CostPeriod = Convert.ToString(Get(month, "month") ?? "");

                var session = AsDict(Get(payload, "currentTelegramSession"));
                summary.SessionKey = Convert.ToString(Get(session, "sessionKey") ?? "");
                summary.SessionTotalTokens = Math.Max(0, ToLong(Get(session, "totalTokens")));
                summary.SessionContextTokens = Math.Max(0, ToLong(Get(session, "contextTokens")));
                summary.SessionContextLimit = Math.Max(0, ToLong(Get(session, "contextLimit")));

                var buckets = AsList(Get(payload, "buckets"));
                foreach (var item in buckets.Cast<object>().Take(6))
                {
                    var b = AsDict(item);
                    var label = Convert.ToString(Get(b, "label") ?? Get(b, "key") ?? "-");
                    summary.Lines.Add(
                        "缓存 · " + Trim(label, 36) +
                        " · 入 " + FormatTokens(Math.Max(0, ToLong(Get(b, "inputTokens")))) +
                        "｜出 " + FormatTokens(Math.Max(0, ToLong(Get(b, "outputTokens")))) +
                        "｜缓存 " + FormatTokens(Math.Max(0, ToLong(Get(b, "cacheReadTokens"))) + Math.Max(0, ToLong(Get(b, "cacheWriteTokens")))) +
                        "｜成本 " + FormatOptionalUsd(Get(b, "estimatedCost")));
                }

                if (!string.IsNullOrWhiteSpace(summary.SessionKey))
                    summary.Lines.Insert(0, "当前入口 · " + Trim(summary.SessionKey, 64));
                summary.Available = summary.Status == "ok" || summary.TotalTokens > 0 || summary.SessionTotalTokens > 0;
                return summary;
            }
            catch (Exception ex)
            {
                summary.Error = ex.Message;
                return summary;
            }
        }

        void FillReliabilitySnapshot(Snapshot s)
        {
            var reliability = ReadReliabilitySummary();
            if (!reliability.Available)
            {
                if (!string.IsNullOrWhiteSpace(reliability.Error))
                    s.Logs.Insert(0, "可靠性 · 暂无 observer 缓存：" + Trim(reliability.Error, 80));
                return;
            }

            var age = reliability.AgeMs >= 0 ? Age(reliability.AgeMs) + "前" : "-";
            var statusLabel = ReliabilityStatusLabel(reliability.Status);
            var line = "可靠性 · " + statusLabel + " · " + Trim(reliability.Summary, 96) + " · " + age;
            s.ReliabilityStatus = reliability.Status;
            s.ReliabilitySummaryText = reliability.Summary;
            s.ExternalNetworkIssue = reliability.Kinds.Any(IsExternalNetworkReliabilityKind);
            s.Logs.Insert(0, line);
            foreach (var detail in reliability.Lines.Take(3).Reverse())
                s.Logs.Insert(1, "  " + detail);

            var liveEntranceConfirmed = s.GatewayOk && s.TelegramOk;
            if ((reliability.Status == "risk" || reliability.Status == "warn") && string.IsNullOrWhiteSpace(s.StatusLine))
                s.StatusLine = liveEntranceConfirmed
                    ? "当前 gateway 和 Telegram 已确认；最近可靠性事件只作为提醒显示。"
                    : "最近有可靠性事件；控制中心已从本地 observer 缓存读取原因。";
            else if (reliability.Stale && string.IsNullOrWhiteSpace(s.StatusLine))
                s.StatusLine = "可靠性 observer 缓存较旧；主面板仍保持只读缓存模式。";
        }

        bool IsExternalNetworkReliabilityKind(string kind)
        {
            var key = (kind ?? "").Trim().ToLowerInvariant();
            return key == "network_or_provider_failure"
                || key == "telegram_delivery_failed"
                || key == "telegram_processing_failed"
                || key == "telegram_action_failed";
        }

        ReliabilitySummary ReadReliabilitySummary()
        {
            var summary = new ReliabilitySummary();
            try
            {
                var script = "cat ~/.openclaw/monitor-cache/reliability-status.json 2>/dev/null";
                var result = RunProcess("wsl.exe", new[] { "-d", WslDistro, "--", "bash", "-lc", script }, 3000);
                if (!result.Ok || string.IsNullOrWhiteSpace(result.Stdout))
                {
                    summary.Error = "未找到 ~/.openclaw/monitor-cache/reliability-status.json";
                    return summary;
                }

                var payload = AsDict(json.DeserializeObject(ExtractJsonObject(result.Stdout)));
                summary.Status = (Convert.ToString(Get(payload, "status") ?? "") ?? "").Trim().ToLowerInvariant();
                summary.GeneratedAt = Convert.ToString(Get(payload, "generatedAt") ?? "");
                summary.Summary = Convert.ToString(Get(payload, "summary") ?? "");
                summary.AgeMs = UsageCacheAgeMs(summary.GeneratedAt);
                summary.Stale = summary.AgeMs > 5L * 60L * 1000L;

                foreach (var item in AsList(Get(payload, "events")).Cast<object>().Take(4))
                {
                    var row = AsDict(item);
                    var kind = Convert.ToString(Get(row, "kind") ?? "-");
                    var eventSummary = Convert.ToString(Get(row, "summary") ?? "");
                    var at = Convert.ToString(Get(row, "at") ?? "");
                    var ageMs = ToLong(Get(row, "ageMs"));
                    var eventAge = ageMs >= 0 ? Age(ageMs) + "前" : (string.IsNullOrWhiteSpace(at) ? "-" : at);
                    if (!string.IsNullOrWhiteSpace(kind)) summary.Kinds.Add(kind);
                    summary.Lines.Add(ReliabilityKindLabel(kind) + " · " + Trim(eventSummary, 88) + " · " + eventAge);
                }

                summary.Available = summary.Status == "ok" || summary.Status == "warn" || summary.Status == "risk" || summary.Status == "error";
                if (string.IsNullOrWhiteSpace(summary.Summary) && summary.Available)
                    summary.Summary = summary.Status == "ok" ? "最近未发现静默失败信号" : "observer 记录到可靠性事件";
                return summary;
            }
            catch (Exception ex)
            {
                summary.Error = ex.Message;
                return summary;
            }
        }

        string ReliabilityStatusLabel(string status)
        {
            switch ((status ?? "").Trim().ToLowerInvariant())
            {
                case "risk": return "高风险";
                case "warn": return "需观察";
                case "error": return "读取异常";
                case "ok": return "正常";
                default: return "未知";
            }
        }

        string ReliabilityKindLabel(string kind)
        {
            switch ((kind ?? "").Trim().ToLowerInvariant())
            {
                case "model_overloaded": return "模型过载";
                case "telegram_delivery_failed": return "Telegram 回传失败";
                case "telegram_processing_failed": return "Telegram 处理失败";
                case "telegram_action_failed": return "Telegram 动作失败";
                case "network_or_provider_failure": return "网络/供应商失败";
                case "gateway_shutdown_timeout": return "Gateway 停机超时";
                case "gateway_startup_failed": return "Gateway 启动失败";
                case "gateway_lifecycle_signal": return "Gateway 生命周期";
                case "session_lock": return "Session 锁";
                case "context_overflow": return "上下文溢出";
                default: return Trim(kind, 28);
            }
        }

        void FillCostUsage(Snapshot s)
        {
            var summary = GetCostSummary();
            if (summary.Available)
            {
                s.CostText = FormatUsd(summary.TotalCost);
                s.CostState = summary.TotalCost > 0 ? "work" : "good";
                foreach (var line in summary.Lines.Take(6))
                    s.TokenFlows.Add(line);
            }
            else
            {
                s.CostText = "未记录";
                s.CostState = "warn";
                s.TokenFlows.Add(string.IsNullOrWhiteSpace(summary.Error)
                    ? "成本 · 本地 session 日志里暂时没有 usage.cost；API-key 模型更容易留下成本记录。"
                    : "成本 · 读取失败：" + Trim(summary.Error, 100));
            }
        }

        CostSummary GetCostSummary()
        {
            lock (costLock)
            {
                if (cachedCost.Available && (DateTime.Now - cachedCost.UpdatedAt).TotalSeconds < 60)
                    return cachedCost;
            }

            var fresh = ReadCostSummary();
            lock (costLock)
            {
                cachedCost = fresh;
                return cachedCost;
            }
        }

        CostSummary ReadCostSummary()
        {
            var summary = new CostSummary { UpdatedAt = DateTime.Now };
            try
            {
                var script =
                    "const fs=require('fs');const path=require('path');const root=(process.env.HOME||'')+'/.openclaw/agents/main/sessions';const now=new Date();const monthStartMs=new Date(now.getFullYear(),now.getMonth(),1).getTime();const out={available:false,totalCost:0,buckets:[],error:'',monthStart:monthStartMs};const toMs=v=>{if(v==null)return 0;if(typeof v==='number')return v>1e12?v:v*1000;const n=Number(v);if(Number.isFinite(n)&&n>0)return n>1e12?n:n*1000;const d=Date.parse(String(v));return Number.isFinite(d)?d:0;};try{if(!fs.existsSync(root)){out.error='找不到 session 目录';}else{const map={};const seen=new Set();const add=(o,u,base)=>{const c=u&&u.cost;const value=Number(c&&c.total||0);if(!(value>0))return;const eventMs=toMs(o.timestamp||o.ts||o.createdAt||o.updatedAt||base.timestamp||base.ts)||base.fileM||0;if(eventMs<monthStartMs)return;const provider=String(o.provider||base.provider||'-');const model=String(o.model||base.model||'-');const dedupe=String(o.responseId||o.id||'')||[eventMs,provider,model,value,u.input,u.output,u.cacheRead,u.cacheWrite].join('|');if(seen.has(dedupe))return;seen.add(dedupe);const key=provider+'/'+model;const b=map[key]||(map[key]={key,cost:0,input:0,output:0,cacheRead:0,cacheWrite:0,totalTokens:0,replies:0});b.cost+=value;b.input+=Number(u.input||0);b.output+=Number(u.output||0);b.cacheRead+=Number(u.cacheRead||0);b.cacheWrite+=Number(u.cacheWrite||0);b.totalTokens+=Number(u.totalTokens||0);b.replies+=1;};const visit=(o,base)=>{if(!o||typeof o!=='object')return;if(o.usage&&o.usage.cost)add(o,o.usage,base);if(Array.isArray(o)){for(const v of o)visit(v,base);return;}for(const k of Object.keys(o)){if(k==='config'||k==='redacted')continue;visit(o[k],base);}};const files=fs.readdirSync(root).filter(f=>f.includes('.jsonl')).map(f=>{const p=path.join(root,f);return{f,p,m:fs.statSync(p).mtimeMs};}).filter(f=>f.m>=monthStartMs).sort((a,b)=>b.m-a.m).slice(0,500);for(const file of files){const text=fs.readFileSync(file.p,'utf8');for(const line of text.split(/\\r?\\n/)){if(!line||line.indexOf('usage')<0||line.indexOf('cost')<0)continue;let row;try{row=JSON.parse(line);}catch{continue;}visit(row,{provider:row.provider,model:row.model,timestamp:row.timestamp,ts:row.ts,fileM:file.m});}}out.buckets=Object.values(map).sort((a,b)=>b.cost-a.cost);out.totalCost=out.buckets.reduce((n,b)=>n+b.cost,0);out.available=out.buckets.length>0;}}catch(e){out.error=e&&e.message?e.message:String(e);}console.log(JSON.stringify(out));";
                var result = RunProcess("wsl.exe", new[] { "-d", WslDistro, "--", "node", "-e", script }, 60000);
                if (!result.Ok)
                {
                    summary.Error = Trim(result.Stderr + result.Error, 160);
                    return summary;
                }

                var payload = AsDict(json.DeserializeObject(result.Stdout));
                summary.TotalCost = ToDouble(Get(payload, "totalCost"));
                summary.Available = ToBool(Get(payload, "available"));
                summary.Error = Convert.ToString(Get(payload, "error") ?? "");
                foreach (var item in AsList(Get(payload, "buckets")).Cast<object>().Take(8))
                {
                    var b = AsDict(item);
                    summary.Lines.Add(
                        "成本 · " + Convert.ToString(Get(b, "key") ?? "-") +
                        " · " + FormatUsd(ToDouble(Get(b, "cost"))) +
                        " · " + Math.Max(0, ToLong(Get(b, "replies"))) + " 次回复" +
                        " · 入 " + FormatTokens(Math.Max(0, ToLong(Get(b, "input")))) +
                        "｜出 " + FormatTokens(Math.Max(0, ToLong(Get(b, "output")))) +
                        "｜缓存 " + FormatTokens(Math.Max(0, ToLong(Get(b, "cacheRead"))) + Math.Max(0, ToLong(Get(b, "cacheWrite")))));
                }
                return summary;
            }
            catch (Exception ex)
            {
                summary.Error = ex.Message;
                return summary;
            }
        }

        string TokenSource(string key)
        {
            key = key ?? "";
            if (key.Contains(":telegram:")) return "Telegram";
            if (key.Contains(":subagent:")) return "子任务";
            if (key.EndsWith(":main") || key.Contains(":main:main")) return "主会话";
            if (key.Contains(":slash:")) return "命令";
            return "直接会话";
        }

        void FillAudit(Snapshot s, object auditObj)
        {
            var audit = AsDict(auditObj);
            var findings = AsList(Get(audit, "findings"));
            var warnings = 0;
            var errors = 0;
            foreach (var findingObj in findings)
            {
                var finding = AsDict(findingObj);
                var findingTimeMs = AuditFindingTimestampMs(finding);
                if (findingTimeMs > 0 && findingTimeMs < monitorStartedAtMs) continue;

                var severity = (Convert.ToString(Get(finding, "severity")) ?? "").Trim().ToLowerInvariant();
                if (severity == "error") errors++;
                else if (severity == "warn" || severity == "warning") warnings++;
            }

            s.AuditWarnings = warnings;
            s.AuditErrors = errors;
            if (s.AuditErrors > 0) s.State = "Problem";
        }

        long AuditFindingTimestampMs(Dictionary<string, object> finding)
        {
            var task = AsDict(Get(finding, "task"));
            var flow = AsDict(Get(finding, "flow"));
            var timestamp = Math.Max(
                Math.Max(ToLong(Get(task, "lastEventAt")), ToLong(Get(task, "endedAt"))),
                Math.Max(ToLong(Get(flow, "updatedAt")), ToLong(Get(flow, "endedAt"))));
            timestamp = Math.Max(timestamp, Math.Max(ToLong(Get(task, "startedAt")), ToLong(Get(flow, "createdAt"))));
            timestamp = Math.Max(timestamp, Math.Max(ToLong(Get(task, "createdAt")), ToLong(Get(finding, "updatedAt"))));
            var ageMs = ToLong(Get(finding, "ageMs"));
            if (timestamp <= 0 && ageMs >= 0)
                timestamp = (long)(DateTime.UtcNow - new DateTime(1970, 1, 1)).TotalMilliseconds - ageMs;
            return timestamp;
        }

        void FillLogs(Snapshot s, List<Dictionary<string, object>> logs)
        {
            var interesting = logs
                .Where(l =>
                {
                    var sub = Convert.ToString(Get(l, "subsystem") ?? "");
                    var lvl = Convert.ToString(Get(l, "level") ?? "");
                    return sub.Contains("telegram") || lvl == "error" || lvl == "warn";
                })
                .TakeLastCompat(16)
                .ToList();

            foreach (var log in interesting)
            {
                var time = Convert.ToString(Get(log, "time") ?? "");
                var clock = time.Length >= 19 ? time.Substring(11, 8) : "--:--:--";
                var rawLevel = Convert.ToString(Get(log, "level") ?? "");
                var level = TranslateLogLevel(rawLevel);
                var subsystemRaw = Convert.ToString(Get(log, "subsystem") ?? "");
                var subsystem = Trim(TranslateSubsystem(subsystemRaw), 30);
                var message = Trim(System.Text.RegularExpressions.Regex.Replace(Convert.ToString(Get(log, "message") ?? ""), "\\s+", " "), 115);
                s.Logs.Add(clock + " " + Pad(level, 5) + " " + Pad(subsystem, 30) + " " + message);
            }

            foreach (var log in interesting.Where(IsTaskRelevantIssueLog).TakeLastCompat(4))
            {
                var time = Convert.ToString(Get(log, "time") ?? "");
                var clock = time.Length >= 19 ? time.Substring(11, 8) : "--:--:--";
                var rawLevel = Convert.ToString(Get(log, "level") ?? "");
                var subsystemRaw = Convert.ToString(Get(log, "subsystem") ?? "");
                var message = Trim(System.Text.RegularExpressions.Regex.Replace(Convert.ToString(Get(log, "message") ?? ""), "\\s+", " "), 100);
                var status = rawLevel == "error" ? "\u521a\u5931\u8d25" : "\u9700\u7559\u610f";
                s.Tasks.Add(new[] { IssueTaskLabel(subsystemRaw, message), "\u6700\u8fd1\u9519\u8bef", status, "-", clock + " \u00b7 " + message });
            }

            if (s.Logs.Count == 0) s.Logs.Add("\u6700\u8fd1\u6ca1\u6709 Telegram \u6216\u9519\u8bef\u65e5\u5fd7\u3002");
        }

        void FillConversationActivity(Snapshot s)
        {
            var replyRowAdded = false;
            if (s.TelegramLastInboundAt > 0)
            {
                var inboundAgeMs = MillisecondsSince(s.TelegramLastInboundAt);
                var outboundAfterInbound = s.TelegramLastOutboundAt >= s.TelegramLastInboundAt;
                if (!outboundAfterInbound)
                {
                    var sessionActive = s.LastSessionAgeMs >= 0 && s.LastSessionAgeMs <= freshTaskEventWindowMs;
                    var stuck = inboundAgeMs > 10L * 60L * 1000L && !sessionActive;
                    var status = stuck
                        ? "疑似卡住"
                        : sessionActive ? "正在处理" : inboundAgeMs > freshTaskEventWindowMs ? "等待回复" : "刚收到";
                    s.LocalWorkItems = Math.Max(s.LocalWorkItems, 1);
                    if (stuck && s.State != "Problem") s.State = "Degraded";
                    var detail = sessionActive
                        ? "模型会话最近仍有活动；尚未观察到发出回复"
                        : stuck
                            ? "超过 10 分钟未观察到回复，也没有新的模型会话活动"
                            : "收到用户消息后尚未观察到发出回复";
                    s.Tasks.Insert(0, new[] { "OpenClaw 回复状态", "Telegram", status, Age(inboundAgeMs), detail });
                    replyRowAdded = true;
                    s.StatusLine = string.IsNullOrWhiteSpace(s.StatusLine)
                        ? "Telegram 消息待回复：" + Age(inboundAgeMs)
                        : s.StatusLine + " | 待回复 " + Age(inboundAgeMs);
                    return;
                }
                if (outboundAfterInbound && MillisecondsSince(s.TelegramLastOutboundAt) <= 30L * 60L * 1000L)
                {
                    var outboundAge = MillisecondsSince(s.TelegramLastOutboundAt);
                    s.Tasks.Insert(0, new[] { "OpenClaw 回复状态", "Telegram", "已回复", Age(outboundAge), "最近一条用户消息已经观察到发出回复" });
                    replyRowAdded = true;
                    if (string.IsNullOrWhiteSpace(s.StatusLine)) s.StatusLine = "最近 Telegram 消息已回复：" + Age(outboundAge);
                    return;
                }
            }
            else if (s.TelegramOk)
            {
                s.Tasks.Insert(0, new[] { "OpenClaw 回复状态", "Telegram", "无法判断", "-", "Telegram 通道未返回最近用户消息/回复时间；下方仅能用模型会话辅助判断" });
                replyRowAdded = true;
            }

            if (s.LastSessionAgeMs < 0) return;
            if (s.LastSessionAgeMs > 30L * 60L * 1000L) return;

            var active = s.LastSessionAgeMs <= freshTaskEventWindowMs;
            if (active) s.LocalWorkItems = Math.Max(s.LocalWorkItems, 1);
            var runtime = string.IsNullOrWhiteSpace(s.LastSessionSource) ? "session" : s.LastSessionSource;
            var statusText = active ? "正在处理" : "最近活动";
            var modelDetail = active
                ? (string.IsNullOrWhiteSpace(s.LastSessionModel) ? "-" : s.LastSessionModel) + " · " + (s.TokenContext == "-" ? "token 未读到" : s.TokenContext)
                : "模型会话最近更新；任务可能已结束 · " + (string.IsNullOrWhiteSpace(s.LastSessionModel) ? "-" : s.LastSessionModel);
            s.Tasks.Insert(replyRowAdded ? 1 : 0, new[] { "OpenClaw 模型会话", runtime, statusText, Age(s.LastSessionAgeMs), modelDetail });
            if (active)
            {
                s.StatusLine = string.IsNullOrWhiteSpace(s.StatusLine)
                    ? "OpenClaw 会话正在处理：" + Age(s.LastSessionAgeMs)
                    : s.StatusLine + " | 会话活动 " + Age(s.LastSessionAgeMs);
            }
        }

        void FillTaskTableFallback(Snapshot s)
        {
            if (s.Tasks.Count > 0) return;
            var status = s.State == "Ready" ? "\u7a7a\u95f2" : s.State == "Problem" ? "\u9700\u5904\u7406" : "\u65e0\u6d3b\u52a8";
            var detail = s.State == "Ready"
                ? "\u672a\u68c0\u6d4b\u5230\u5bf9\u8bdd\u5904\u7406 / registered task / TaskFlow / systemd \u4efb\u52a1 / \u76f8\u5173 OS \u8fdb\u7a0b / \u65b0\u4ea7\u7269\u5199\u5165"
                : string.IsNullOrWhiteSpace(s.StatusLine) ? "\u6682\u65e0\u53ef\u5c55\u793a\u7684\u540e\u53f0\u6d3b\u52a8" : Trim(s.StatusLine, 110);
            s.Tasks.Add(new[] { "OpenClaw \u5f53\u524d\u7a7a\u95f2", "\u603b\u89c8", status, "-", detail });
        }

        bool IsTaskRelevantIssueLog(Dictionary<string, object> log)
        {
            var level = Convert.ToString(Get(log, "level") ?? "");
            if (level != "error" && level != "warn") return false;
            if (!LogWithin(log, 30L * 60L * 1000L)) return false;
            var subsystem = (Convert.ToString(Get(log, "subsystem") ?? "") ?? "").ToLowerInvariant();
            var message = (Convert.ToString(Get(log, "message") ?? "") ?? "").ToLowerInvariant();
            var joined = subsystem + " " + message;
            var mediaOrDelivery = joined.Contains("image") || joined.Contains("vision") || joined.Contains("ocr") || joined.Contains("telegram") || joined.Contains("delivery");
            var runtimeFailure = joined.Contains("nameerror") || joined.Contains("exception") || joined.Contains("not supported");
            var scopedTimeout = (joined.Contains("timeout") || joined.Contains("timed out")) &&
                (mediaOrDelivery || joined.Contains("model") || joined.Contains("tool") || joined.Contains("reply"));
            return mediaOrDelivery || runtimeFailure || scopedTimeout;
        }

        bool LogWithin(Dictionary<string, object> log, long windowMs)
        {
            var time = Convert.ToString(Get(log, "time") ?? "");
            DateTimeOffset parsed;
            if (!DateTimeOffset.TryParse(time, out parsed)) return false;
            return Math.Max(0, (DateTimeOffset.Now - parsed).TotalMilliseconds) <= windowMs;
        }

        string IssueTaskLabel(string subsystem, string message)
        {
            var text = ((subsystem ?? "") + " " + (message ?? "")).ToLowerInvariant();
            if (text.Contains("image") || text.Contains("vision") || text.Contains("ocr")) return "\u56fe\u7247\u9605\u8bfb/\u8bc6\u522b";
            if (text.Contains("telegram") || text.Contains("delivery")) return "Telegram/\u6d88\u606f\u6295\u9012";
            if (text.Contains("nameerror") || text.Contains("exception")) return "\u811a\u672c\u5f02\u5e38";
            return "OpenClaw \u6700\u8fd1\u9519\u8bef";
        }

        string ServiceStatusLabel(string state)
        {
            state = (state ?? "").ToLowerInvariant();
            if (state.StartsWith("failed")) return "\u521a\u5931\u8d25";
            if (state.StartsWith("deactivating")) return "\u7591\u4f3c\u6536\u5c3e\u5361\u4f4f";
            if (state.StartsWith("activating")) return "\u542f\u52a8\u4e2d";
            if (state.StartsWith("active")) return "\u8fd0\u884c\u4e2d";
            return string.IsNullOrWhiteSpace(state) ? "\u672a\u77e5" : state;
        }

        string ProcessStatusLabel(string etime, string cpu, string stat)
        {
            var cpuValue = ToDouble(cpu);
            var longRunning = ProcessElapsedLooksLong(etime);
            var sleep = !string.IsNullOrWhiteSpace(stat) && stat.StartsWith("S", StringComparison.OrdinalIgnoreCase);
            if (longRunning && cpuValue < 0.1 && sleep) return "\u7591\u4f3c\u9759\u9ed8";
            if (cpuValue >= 3) return "\u8fd0\u884c\u4e2d";
            return "\u8fdb\u7a0b\u5b58\u5728";
        }

        bool ProcessElapsedLooksLong(string etime)
        {
            if (string.IsNullOrWhiteSpace(etime)) return false;
            etime = etime.Trim();
            if (etime.Contains("-")) return true;
            var parts = etime.Split(':');
            int hours;
            return parts.Length == 3 && int.TryParse(parts[0], out hours) && hours >= 1;
        }

        bool ProcessElapsedOverMinutes(string etime, int minutes)
        {
            if (string.IsNullOrWhiteSpace(etime)) return false;
            etime = etime.Trim();
            if (etime.Contains("-")) return true;
            var parts = etime.Split(':');
            int hours;
            int mins;
            if (parts.Length == 3)
            {
                return int.TryParse(parts[0], out hours) && hours > 0 ||
                    int.TryParse(parts[1], out mins) && mins >= minutes;
            }
            return parts.Length == 2 && int.TryParse(parts[0], out mins) && mins >= minutes;
        }

        string TelegramTokenState(long total)
        {
            if (total >= 160000) return "Risk";
            if (total >= 80000) return "Warn";
            return "Good";
        }

        string TelegramTokenReason(long total)
        {
            if (total >= 200000) return "200K+ 强烈建议 /new + handoff";
            if (total >= 160000) return "160K-200K 高风险，只做短指令 / 状态查询 / 收尾";
            if (total >= 120000) return "120K-160K 建议 handoff，长任务后台化";
            if (total >= 80000) return "80K-120K 需观察，避免贴长日志/长 diff/大段源码";
            return "<80K 正常";
        }

        Tuple<bool, object, string> RunOpenClawJson(string[] args, int timeoutMs)
        {
            var all = new List<string> { "-d", WslDistro, "--", "bash", "-lc", BashCommand(args) };
            var result = RunProcess("wsl.exe", all.ToArray(), timeoutMs);
            if (!result.Ok) return Tuple.Create(false, (object)null, result.Stderr + result.Error);
            try
            {
                return Tuple.Create(true, json.DeserializeObject(ExtractJsonObject(result.Stdout)), "");
            }
            catch (Exception ex)
            {
                return Tuple.Create(false, (object)null, "JSON 解析失败：" + ex.Message);
            }
        }

        Tuple<bool, string, string> RunOpenClawText(string[] args, int timeoutMs)
        {
            var all = new List<string> { "-d", WslDistro, "--", "bash", "-lc", BashCommand(args) };
            var result = RunProcess("wsl.exe", all.ToArray(), timeoutMs);
            return Tuple.Create(result.Ok, result.Stdout, result.Stderr + result.Error);
        }

        static string ExtractJsonObject(string text)
        {
            if (string.IsNullOrWhiteSpace(text)) return "{}";
            var start = text.IndexOf('{');
            if (start < 0) return text;
            var depth = 0;
            var inString = false;
            var escape = false;
            for (var i = start; i < text.Length; i++)
            {
                var ch = text[i];
                if (escape)
                {
                    escape = false;
                    continue;
                }
                if (ch == '\\' && inString)
                {
                    escape = true;
                    continue;
                }
                if (ch == '"')
                {
                    inString = !inString;
                    continue;
                }
                if (inString) continue;
                if (ch == '{') depth++;
                else if (ch == '}')
                {
                    depth--;
                    if (depth == 0) return text.Substring(start, i - start + 1);
                }
            }
            return text.Substring(start);
        }

        static string ExtractJsonValue(string text)
        {
            if (string.IsNullOrWhiteSpace(text)) return "{}";
            var objectStart = text.IndexOf('{');
            var arrayStart = text.IndexOf('[');
            var start = objectStart >= 0 && arrayStart >= 0 ? Math.Min(objectStart, arrayStart) : Math.Max(objectStart, arrayStart);
            if (start < 0) return text;
            var opener = text[start];
            var closer = opener == '[' ? ']' : '}';
            var depth = 0;
            var inString = false;
            var escape = false;
            for (var i = start; i < text.Length; i++)
            {
                var ch = text[i];
                if (escape)
                {
                    escape = false;
                    continue;
                }
                if (ch == '\\' && inString)
                {
                    escape = true;
                    continue;
                }
                if (ch == '"')
                {
                    inString = !inString;
                    continue;
                }
                if (inString) continue;
                if (ch == opener) depth++;
                else if (ch == closer)
                {
                    depth--;
                    if (depth == 0) return text.Substring(start, i - start + 1);
                }
            }
            return text.Substring(start);
        }

        Tuple<bool, string, string> RunWorkspaceActivity()
        {
            var script =
                "cd \"$HOME/.openclaw/workspace\" 2>/dev/null || exit 0\n" +
                "seen=' '\n" +
                "emit_proc() { role=\"$1\"; pid=\"$2\"; etime=\"$3\"; cpu=\"$4\"; stat=\"$5\"; shift 5; args=\"$*\"; [ -n \"$pid\" ] || return; case \"$seen\" in *\" $pid \"*) return;; esac; seen=\"$seen$pid \"; printf 'LOCALPROC\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\n' \"$role\" \"$pid\" \"$etime\" \"$cpu\" \"$stat\" \"$(printf '%s' \"$args\" | cut -c1-180)\"; }\n" +
                "emit_pid() { role=\"$1\"; pid=\"$2\"; [ -n \"$pid\" ] || return; row=$(ps -p \"$pid\" -o pid=,etime=,pcpu=,stat=,args= 2>/dev/null); [ -n \"$row\" ] || return; read -r p etime cpu stat args <<EOF\n$row\nEOF\nemit_proc \"$role\" \"$p\" \"$etime\" \"$cpu\" \"$stat\" \"$args\"; }\n" +
                "role_for_args() { case \"$1\" in *market_immersion.py*|*run_market_immersion.sh*) echo \"\u6bcf\u65e5\u5feb\u8baf\";; *people_daily_workflow.py*|*run_people_daily_deep_read.sh*) echo \"\u4eba\u6c11\u65e5\u62a5\u6df1\u8bfb\";; *market_feed_snapshot.py*) echo \"\u884c\u60c5\u5feb\u7167\";; *openclaw-message*) echo \"OpenClaw \u6d88\u606f\u6295\u9012\";; *rapidocr*|*paddleocr*|*tesseract*) echo \"\u56fe\u7247/OCR\";; *) echo \"OpenClaw \u76f8\u5173\u8fdb\u7a0b\";; esac; }\n" +
                "emit_service() { name=\"$1\"; unit=\"$2\"; systemctl --user cat \"$unit\" >/dev/null 2>&1 || return; active=$(systemctl --user is-active \"$unit\" 2>/dev/null || true); sub=$(systemctl --user show \"$unit\" -p SubState --value 2>/dev/null | head -1); pid=$(systemctl --user show \"$unit\" -p MainPID --value 2>/dev/null | head -1); since=$(systemctl --user show \"$unit\" -p ActiveEnterTimestamp --value 2>/dev/null | head -1); since_s=$(date -d \"$since\" +%s 2>/dev/null || echo 0); now_s=$(date +%s); age_s=0; [ \"$since_s\" -gt 0 ] 2>/dev/null && age_s=$((now_s-since_s)); last=$(journalctl --user -u \"$unit\" -n 1 --no-pager -o short-iso 2>/dev/null | cut -c1-180); case \"$active\" in active|activating|deactivating|failed) printf 'SERVICE\\t%s\\t%s/%s\\t%s\\t%s\\t%s\\t%s\\n' \"$name\" \"$active\" \"$sub\" \"$unit\" \"$pid\" \"$age_s\" \"$last\";; esac; }\n" +
                "pidfile=\"memory/continuous-task-status/steinsgate-kurisu.pid\"; [ -f \"$pidfile\" ] && emit_pid \"\u5b66\u4e60 daemon\" \"$(tr -dc '0-9' < \"$pidfile\" 2>/dev/null)\"\n" +
                "ps -eo pid=,etime=,pcpu=,stat=,args= | grep -E 'continuous_learning_daemon\\.py|steinsgate_visible_supervisor\\.py|market_immersion\\.py|run_market_immersion\\.sh|people_daily_workflow\\.py|run_people_daily_deep_read\\.sh|market_feed_snapshot\\.py|openclaw-message|rapidocr|paddleocr|tesseract' | grep -v -E 'grep -E|OpenClawMonitor|systemctl --user|journalctl --user|ps -eo pid=' | while read -r pid etime cpu stat args; do role=$(role_for_args \"$args\"); emit_proc \"$role\" \"$pid\" \"$etime\" \"$cpu\" \"$stat\" \"$args\"; done\n" +
                "emit_service \"OpenClaw \u7f51\u7edc watchdog\" openclaw-netwatch.service\n" +
                "emit_service \"\u6bcf\u65e5\u5feb\u8baf morning\" openclaw-market-immersion-morning.service\n" +
                "emit_service \"\u6bcf\u65e5\u5feb\u8baf midday\" openclaw-market-immersion-midday.service\n" +
                "emit_service \"\u6bcf\u65e5\u5feb\u8baf close\" openclaw-market-immersion-close.service\n" +
                "emit_service \"\u6bcf\u65e5\u5feb\u8baf night\" openclaw-market-immersion-night.service\n" +
                "emit_service \"\u4eba\u6c11\u65e5\u62a5\u6df1\u8bfb\" openclaw-people-daily-deep-read.service\n" +
                "emit_service \"\u884c\u60c5\u5feb\u7167\" openclaw-market-feed-snapshot.service\n" +
                "watchlog=\"memory/continuous-task-status/steinsgate-kurisu-watchdog.log\"; [ -f \"$watchlog\" ] && printf 'WATCHDOG\\t%s\\t%s\\n' \"$(stat -c '%Y' \"$watchlog\" 2>/dev/null)\" \"$(tail -1 \"$watchlog\" 2>/dev/null | cut -c1-160)\"\n" +
                "for root in steinsgate memory/continuous-task-status market-immersion people-daily-deep-read-preview media; do [ -e \"$root\" ] || continue; find \"$root\" -maxdepth 4 -type f -mmin -180 \\( -name '*.md' -o -name '*.json' -o -name '*.jsonl' -o -name '*.txt' -o -name '*.log' \\) -printf 'ARTIFACT\\t%T@\\t%p\\n' 2>/dev/null; done | sort -k2,2nr | head -20\n";
            var result = RunProcess("wsl.exe", new[] { "-d", WslDistro, "--", "bash", "-lc", script }, 15000);
            return Tuple.Create(result.Ok, result.Stdout, result.Stderr + result.Error);
        }

        void FillWorkspaceActivity(Snapshot s, Tuple<bool, string, string> data)
        {
            if (data == null || !data.Item1 || string.IsNullOrWhiteSpace(data.Item2)) return;

            var artifactRows = 0;
            long latestArtifactMs = 0;
            var currentArtifacts = new Dictionary<string, long>();
            foreach (var line in data.Item2.Split(new[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries))
            {
                var parts = line.Split(new[] { '\t' });
                if (parts.Length >= 2 && parts[0] == "DAEMON")
                {
                    s.LocalDaemonActive = true;
                    s.LocalWorkItems = Math.Max(s.LocalWorkItems, 1);
                    var pid = parts[1];
                    var detail = parts.Length >= 3 ? Trim(parts[2], 70) : "";
                    s.Tasks.Add(new[] { "本地学习 daemon", "本地进程", "运行中", "-", "PID " + pid + (string.IsNullOrWhiteSpace(detail) ? "" : " · " + detail) });
                    continue;
                }

                if (parts.Length >= 7 && parts[0] == "LOCALPROC")
                {
                    s.LocalDaemonActive = true;
                    s.LocalWorkItems = Math.Max(s.LocalWorkItems, 1);
                    var role = parts[1];
                    var pid = parts[2];
                    var etime = parts[3];
                    var cpu = parts[4];
                    var stat = parts[5];
                    var detail = Trim(parts[6], 82);
                    var last = "PID " + pid + " \u00b7 CPU " + (string.IsNullOrWhiteSpace(cpu) ? "-" : cpu + "%") + " \u00b7 " + stat + (string.IsNullOrWhiteSpace(detail) ? "" : " \u00b7 " + detail);
                    s.Tasks.Add(new[] { Trim(role, 42), "OS \u8fdb\u7a0b", ProcessStatusLabel(etime, cpu, stat), string.IsNullOrWhiteSpace(etime) ? "-" : etime, last });
                    continue;
                }

                if (parts.Length >= 7 && parts[0] == "SERVICE")
                {
                    var state = parts[2];
                    var unit = parts[3];
                    var activeService = state.StartsWith("active", StringComparison.OrdinalIgnoreCase) || state.StartsWith("activating", StringComparison.OrdinalIgnoreCase) || state.StartsWith("deactivating", StringComparison.OrdinalIgnoreCase);
                    var monitorOnly = unit == "openclaw-netwatch.service";
                    if (activeService && !monitorOnly)
                    {
                        s.LocalDaemonActive = true;
                        s.LocalWorkItems = Math.Max(s.LocalWorkItems, 1);
                    }
                    var pid = parts[4];
                    var ageSec = Math.Max(0, ToLong(parts[5]));
                    var last = string.IsNullOrWhiteSpace(parts[6]) ? unit : unit + " \u00b7 " + Trim(parts[6], 100);
                    if (!string.IsNullOrWhiteSpace(pid) && pid != "0") last = "PID " + pid + " \u00b7 " + last;
                    var status = monitorOnly && state.StartsWith("active", StringComparison.OrdinalIgnoreCase) ? "\u5b88\u62a4\u4e2d" : ServiceStatusLabel(state);
                    s.Tasks.Add(new[] { Trim(parts[1], 42), "systemd \u670d\u52a1", status, ageSec > 0 ? Age(ageSec * 1000) : "-", last });
                    continue;
                }

                if (parts.Length >= 3 && parts[0] == "WATCHDOG")
                {
                    double seconds = 0;
                    if (double.TryParse(parts[1], NumberStyles.Float, CultureInfo.InvariantCulture, out seconds))
                    {
                        var watchdogMs = (long)(seconds * 1000);
                        var text = "watchdog 最近检查 " + AgeSince(watchdogMs);
                        if (!string.IsNullOrWhiteSpace(parts[2])) text += " · " + Trim(parts[2], 80);
                        s.StatusLine = string.IsNullOrWhiteSpace(s.StatusLine) ? text : s.StatusLine + " | " + text;
                    }
                    continue;
                }

                if (parts.Length >= 3 && parts[0] == "ARTIFACT")
                {
                    double seconds = 0;
                    if (double.TryParse(parts[1], NumberStyles.Float, CultureInfo.InvariantCulture, out seconds))
                    {
                        var artifactMs = (long)(seconds * 1000);
                        latestArtifactMs = Math.Max(latestArtifactMs, artifactMs);
                        currentArtifacts[parts[2]] = artifactMs;
                    }

                    artifactRows++;
                }
            }

            var changedArtifacts = new List<Tuple<string, long>>();
            lock (artifactLock)
            {
                if (artifactBaselineReady)
                {
                    foreach (var item in currentArtifacts)
                    {
                        long previous;
                        if (!previousArtifactMtimes.TryGetValue(item.Key, out previous) || item.Value > previous + 500)
                            changedArtifacts.Add(Tuple.Create(item.Key, item.Value));
                    }
                }
                previousArtifactMtimes = currentArtifacts;
                artifactBaselineReady = true;
            }

            foreach (var item in changedArtifacts.OrderByDescending(x => x.Item2).Take(4))
            {
                var name = Path.GetFileName(item.Item1);
                s.Tasks.Add(new[] { Trim(name, 42), "产物写入", "刚更新", AgeSince(item.Item2), "连续刷新检测到写入" });
            }

            if (artifactRows > 0)
            {
                s.LocalWorkAge = AgeSince(latestArtifactMs);
                if (changedArtifacts.Count > 0)
                    s.LocalWorkItems = Math.Max(s.LocalWorkItems, 1);
                var label = changedArtifacts.Count > 0
                    ? "检测到产物写入"
                    : s.LocalDaemonActive ? "本地 daemon + 最近产物" : "最近产物";
                var suffix = changedArtifacts.Count > 0
                    ? ""
                    : s.LocalDaemonActive ? "" : "（不计为正在运行任务）";
                s.StatusLine = string.IsNullOrWhiteSpace(s.StatusLine)
                    ? label + " " + s.LocalWorkAge + suffix
                    : s.StatusLine + " | " + label + " " + s.LocalWorkAge + suffix;
            }
            else if (s.LocalDaemonActive)
            {
                s.StatusLine = string.IsNullOrWhiteSpace(s.StatusLine)
                    ? "本地 daemon 运行中"
                    : s.StatusLine + " | 本地 daemon 运行中";
            }
        }

        string BashCommand(string[] args)
        {
            var parts = new List<string> { "\"$OPENCLAW_BIN\"" };
            parts.AddRange(args.Select(ShellQuote));
            return OpenClawBootstrapScript() + string.Join(" ", parts);
        }

        string OpenClawBootstrapScript()
        {
            return "OPENCLAW_BIN=" + ShellQuote(OpenClawAbsolutePath) + "\n" +
                "[ -x \"$OPENCLAW_BIN\" ] || OPENCLAW_BIN=$(command -v openclaw 2>/dev/null || true)\n" +
                "[ -n \"$OPENCLAW_BIN\" ] || exit 127\n";
        }

        string ShellQuote(string arg)
        {
            if (arg == null) return "''";
            return "'" + arg.Replace("'", "'\"'\"'") + "'";
        }

        CommandResult RunProcess(string file, string[] args, int timeoutMs)
        {
            var psi = new ProcessStartInfo
            {
                FileName = file,
                Arguments = string.Join(" ", args.Select(QuoteArg)),
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8
            };
            using (var proc = new Process { StartInfo = psi })
            {
                proc.Start();
                var stdout = proc.StandardOutput.ReadToEndAsync();
                var stderr = proc.StandardError.ReadToEndAsync();
                if (!proc.WaitForExit(timeoutMs))
                {
                    try { proc.Kill(); } catch { }
                    return new CommandResult { Ok = false, ExitCode = -1, Error = "命令超时" };
                }
                return new CommandResult
                {
                    Ok = proc.ExitCode == 0,
                    ExitCode = proc.ExitCode,
                    Stdout = stdout.Result,
                    Stderr = stderr.Result
                };
            }
        }

        string QuoteArg(string arg)
        {
            if (arg == null) return "\"\"";
            if (arg.IndexOfAny(new[] { ' ', '\t', '"' }) < 0) return arg;
            return "\"" + arg.Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
        }

        void Render(Snapshot s)
        {
            updated.Text = "";
            heroTitle.Text = HeroTitle(s);
            heroDetail.Text = HeroDetail(s);
            heroTitle.ForeColor = HeroColor(s);
            var showStartupProgress = s.StartupProgress > 0 && s.StartupProgress < 100;
            startupProgressPanel.Visible = showStartupProgress;
            if (showStartupProgress)
            {
                startupProgressBar.Value = Math.Max(startupProgressBar.Minimum, Math.Min(startupProgressBar.Maximum, s.StartupProgress));
                startupProgressText.Text = s.StartupProgress + "% · " + s.StartupStep + " · " + s.StartupProgressText;
            }
            overall.Value.Text = DisplayState(s.State);
            SetCard(overall, s.State == "Problem" ? "bad" : s.State == "Working" ? "work" : s.State == "Ready" ? "good" : "warn");
            gateway.Value.Text = s.GatewayText;
            SetCard(gateway, s.GatewayOk ? "good" : s.GatewaySoftFailure ? "warn" : "bad");
            lastGatewayOk = s.GatewayOk;
            lastOpenClawServiceActive = s.OpenClawServiceActive;
            UpdateOpenClawPowerUi();
            var registeredWork = Math.Max(s.RunningTasks, s.FlowActive);
            var backgroundTotal = registeredWork + s.FlowBlocked + s.FlowCancelRequested + s.LocalWorkItems;
            tasks.Value.Text = backgroundTotal.ToString();
            SetCard(tasks, backgroundTotal > 0 ? "work" : "good");
            audit.Value.Text = s.AuditWarnings + " 提醒 / " + s.AuditErrors + " 错误";
            SetCard(audit, s.AuditErrors > 0 ? "bad" : s.AuditWarnings > 0 ? "warn" : "good");
            session.Value.Text = s.RecentSessionAge;
            SetCard(session, s.RecentSessionAge == "-" ? "warn" : "good");

            tokenTotal.Value.Text = s.TokenContext != "-" ? s.TokenContext : FormatTokens(s.TokenTotal);
            SetCard(tokenTotal, s.TokenTotal > 0 ? "work" : "warn");
            tokenInput.Value.Text = FormatTokens(s.TokenInput);
            SetCard(tokenInput, s.TokenInput > 0 ? "good" : "warn");
            tokenOutput.Value.Text = FormatTokens(s.TokenOutput);
            SetCard(tokenOutput, s.TokenOutput > 0 ? "good" : "warn");
            tokenCache.Value.Text = FormatTokens(s.TokenCacheRead);
            SetCard(tokenCache, s.TokenCacheRead > 0 ? "good" : "warn");
            tokenCost.Value.Text = s.CostText;
            SetCard(tokenCost, s.CostState);
            tokenSectionVisible = s.UsageCacheVisible;
            if (tokenHeader != null) tokenHeader.Visible = tokenSectionVisible;
            foreach (var card in new[] { tokenTotal, tokenInput, tokenOutput, tokenCache, tokenCost })
                card.Panel.Visible = tokenSectionVisible;
            if (costHintPopup != null) costHintPopup.Visible = false;
            LayoutUi();

            taskGrid.Rows.Clear();
            foreach (var row in s.Tasks) taskGrid.Rows.Add(row);
            taskGrid.Visible = false;
            if (taskHeader != null) taskHeader.Visible = false;

            sessionList.Items.Clear();
            foreach (var row in s.Sessions) sessionList.Items.Add(row);
            if (s.Sessions.Count == 0) sessionList.Items.Add("暂未读取到会话数据。");

            logList.Items.Clear();
            foreach (var row in s.Logs) logList.Items.Add(row);

            collabStatusLabel.Text = string.IsNullOrWhiteSpace(s.CollabStatus) ? "" : "🤖 协作状态: " + s.CollabStatus.Replace("\n", " | ");

            statusLine.Text = s.StatusLine;
        }

        string HeroTitle(Snapshot s)
        {
            if (s.State == "Problem") return "OpenClaw 不可用";
            if (s.State == "Degraded" && s.GatewaySoftFailure) return "OpenClaw 运行中";
            if (s.State == "Degraded" && s.GatewayOk) return "OpenClaw 可用";
            if (s.State == "Degraded") return "OpenClaw 需检查";
            if (s.State == "Working" && s.GatewaySoftFailure) return "OpenClaw 运行中";
            if (s.State == "Working") return "OpenClaw 可用";
            if (s.State == "Ready") return "OpenClaw 可用";
            return "OpenClaw 未确认";
        }

        string HeroDetail(Snapshot s)
        {
            if (s.State == "Problem")
            {
                if (!s.OpenClawServiceActive) return "未看到本地 gateway 服务、进程或端口；如果 Telegram 仍可用，请点“诊断”核对。";
                if (!s.GatewayOk) return "控制中心只读到部分本地 gateway 事实；不代表 OpenClaw 不可用。";
                if (!s.TelegramOk) return "网关可连接，但 Telegram 未连接或未配置。";
                if (s.AuditErrors > 0) return "任务审计有错误。请查看提醒和日志。";
                return "有项目需要处理。";
            }
            if (s.State == "Degraded")
            {
                if (s.GatewaySoftFailure) return "本地服务在运行；本轮轻探测没有完整返回。";
                if (s.ExternalNetworkIssue && s.GatewayOk) return "本地 Gateway 可用；最近请求失败已放入提醒，不代表当前不可用。";
                return "有项目需要查看；点“诊断”看具体原因。";
            }
            if (s.State == "Working" && s.GatewaySoftFailure) return "本地服务在运行；本轮轻探测超时。";
            if (s.State == "Working") return "本地 Gateway 可用；后台有活动正在运行。";
            if (s.State == "Ready") return "本地 Gateway 可用；Telegram 端到端状态请看诊断或最近提醒。";
            return "还没有完成可用性检查。";
        }

        Color HeroColor(Snapshot s)
        {
            if (s.State == "Problem") return Color.FromArgb(238, 96, 96);
            if (s.State == "Degraded") return Color.FromArgb(229, 176, 75);
            if (s.State == "Working") return Color.FromArgb(86, 160, 220);
            if (s.State == "Ready") return Color.FromArgb(84, 190, 130);
            return Color.FromArgb(229, 176, 75);
        }

        string DisplayState(string state)
        {
            if (state == "Problem") return "不可用";
            if (state == "Degraded") return "需检查";
            if (state == "Working") return "可用";
            if (state == "Ready") return "可用";
            return "未确认";
        }

        static string TranslateTaskStatus(string status)
        {
            var key = (status ?? "").Trim().ToLowerInvariant();
            if (key == "running") return "运行中";
            if (key == "pending") return "等待中";
            if (key == "queued") return "排队中";
            if (key == "complete" || key == "completed" || key == "done") return "已完成";
            if (key == "failed" || key == "error") return "失败";
            if (key == "cancelled" || key == "canceled") return "已取消";
            return string.IsNullOrWhiteSpace(status) ? "-" : status;
        }

        static string TranslateLogLevel(string level)
        {
            var key = (level ?? "").Trim().ToLowerInvariant();
            if (key == "error") return "错误";
            if (key == "warn" || key == "warning") return "提醒";
            if (key == "info") return "信息";
            if (key == "debug") return "调试";
            return string.IsNullOrWhiteSpace(level) ? "-" : level.ToUpperInvariant();
        }

        static string TranslateSubsystem(string subsystem)
        {
            var key = subsystem ?? "";
            if (key.Contains("gateway/channels/telegram")) return "Telegram 通道";
            if (key.Contains("diagnostic")) return "诊断";
            if (key.Contains("gateway")) return "网关";
            if (key.Contains("telegram")) return "Telegram";
            return key;
        }

        void AddCostHint()
        {
            const string text = "本自然月累计，月初清零；离线估算约每 10 分钟更新。";
            var info = new InfoBadge
            {
              Location = new Point(86, 12),
                Size = new Size(15, 15)
            };
            tokenCost.Panel.Controls.Add(info);
            info.BringToFront();

            costHintPopup = new RoundedPanel
            {
                Location = new Point(tokenCost.Panel.Left, tokenCost.Panel.Bottom + 8),
                Size = new Size(300, 36),
                BackColor = Color.FromArgb(248, 250, 252),
                BorderColor = Color.FromArgb(203, 213, 225),
                Radius = 12,
                Visible = false
            };
            var hintText = new Label
            {
                Text = text,
                Location = new Point(12, 8),
                Size = new Size(276, 20),
                AutoEllipsis = true,
                ForeColor = Color.FromArgb(51, 65, 85),
                Font = new Font("Microsoft YaHei UI", 8.5f),
                BackColor = Color.Transparent
            };
            costHintPopup.Controls.Add(hintText);
            Controls.Add(costHintPopup);

            EventHandler show = (s, e) =>
            {
                var measured = TextRenderer.MeasureText(text, hintText.Font);
                var width = Math.Min(Math.Max(measured.Width + 28, 190), Math.Max(190, ClientSize.Width - 56));
                var height = Math.Max(32, measured.Height + 16);
                var x = Math.Max(28, Math.Min(tokenCost.Panel.Right - width, ClientSize.Width - width - 28));
                var y = tokenCost.Panel.Top - height - 8;
                if (y < 28)
                    y = tokenCost.Panel.Bottom + 8;
                if (y + height > ClientSize.Height - 28)
                    y = Math.Max(28, ClientSize.Height - height - 28);
                costHintPopup.SetBounds(x, y, width, height);
                hintText.SetBounds(12, Math.Max(6, (height - measured.Height) / 2), width - 24, measured.Height + 2);
                costHintPopup.Visible = true;
                costHintPopup.BringToFront();
            };
            EventHandler hide = (s, e) => BeginInvoke(new Action(() =>
            {
                var p = PointToClient(Cursor.Position);
                if (!tokenCost.Panel.Bounds.Contains(p) && !costHintPopup.Bounds.Contains(p))
                    costHintPopup.Visible = false;
            }));
            tokenCost.Panel.MouseEnter += show;
            tokenCost.Value.MouseEnter += show;
            info.MouseEnter += show;
            costHintPopup.MouseEnter += show;
            tokenCost.Panel.MouseLeave += hide;
            tokenCost.Value.MouseLeave += hide;
            info.MouseLeave += hide;
            costHintPopup.MouseLeave += hide;
        }

        void SetCard(Card card, string state)
        {
            if (state == "good") card.Value.ForeColor = Color.FromArgb(84, 190, 130);
            else if (state == "bad") card.Value.ForeColor = Color.FromArgb(238, 96, 96);
            else if (state == "warn") card.Value.ForeColor = Color.FromArgb(229, 176, 75);
            else if (state == "work") card.Value.ForeColor = Color.FromArgb(86, 160, 220);
            else card.Value.ForeColor = Color.White;
        }

        static Dictionary<string, object> AsDict(object value)
        {
            return value as Dictionary<string, object> ?? new Dictionary<string, object>();
        }

        static ArrayList AsList(object value)
        {
            if (value is ArrayList) return (ArrayList)value;
            var array = value as object[];
            if (array != null)
            {
                var list = new ArrayList();
                list.AddRange(array);
                return list;
            }
            var enumerable = value as IEnumerable;
            if (enumerable != null && !(value is string))
            {
                var list = new ArrayList();
                foreach (var item in enumerable) list.Add(item);
                return list;
            }
            return new ArrayList();
        }

        static object Get(Dictionary<string, object> dict, string key)
        {
            if (dict == null) return null;
            object value;
            return dict.TryGetValue(key, out value) ? value : null;
        }

        static object First(ArrayList list)
        {
            return list != null && list.Count > 0 ? list[0] : null;
        }

        static bool ToBool(object value)
        {
            if (value is bool) return (bool)value;
            bool parsed;
            return bool.TryParse(Convert.ToString(value), out parsed) && parsed;
        }

        static long ToLong(object value)
        {
            if (value == null) return -1;
            try { return Convert.ToInt64(value); } catch { return -1; }
        }

        static double ToDouble(object value)
        {
            if (value == null) return 0;
            try { return Convert.ToDouble(value); } catch { return 0; }
        }

        static string AgeSince(long epochMs)
        {
            if (epochMs <= 0) return "-";
            var dt = DateTimeOffset.FromUnixTimeMilliseconds(epochMs).LocalDateTime;
            return Age((long)Math.Max(0, (DateTime.Now - dt).TotalMilliseconds));
        }

        static string AgeSince(string timestamp)
        {
            var ageMs = AgeSinceMs(timestamp);
            return ageMs >= 0 ? Age(ageMs) : "-";
        }

        static long AgeSinceMs(string timestamp)
        {
            if (string.IsNullOrWhiteSpace(timestamp)) return -1;
            DateTimeOffset parsed;
            if (!DateTimeOffset.TryParse(timestamp, CultureInfo.InvariantCulture, DateTimeStyles.AssumeLocal, out parsed)) return -1;
            return (long)Math.Max(0, (DateTimeOffset.Now - parsed.ToLocalTime()).TotalMilliseconds);
        }

        static long MillisecondsSince(long epochMs)
        {
            if (epochMs <= 0) return long.MaxValue;
            return Math.Max(0, DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() - epochMs);
        }

        static string Age(long ms)
        {
            if (ms < 0) return "-";
            if (ms < 1000) return ms + "毫秒";
            var sec = ms / 1000;
            if (sec < 60) return sec + "秒";
            var min = sec / 60;
            if (min < 60) return min + "分";
            var hr = min / 60;
            if (hr < 48) return hr + "小时";
            return (hr / 24) + "天";
        }

        static string FormatTokens(long value)
        {
            if (value <= 0) return "-";
            if (value >= 1000000) return (value / 1000000d).ToString("0.#") + "M";
            if (value >= 1000) return (value / 1000d).ToString("0.#") + "K";
            return value.ToString();
        }

        static string FormatUsd(double value)
        {
            if (value <= 0) return "$0.00";
            if (value < 0.01) return "$" + value.ToString("0.0000");
            return "$" + value.ToString("0.00");
        }

        static string FormatOptionalUsd(object value)
        {
            if (value == null) return "-";
            var parsed = ToDouble(value);
            return parsed > 0 ? FormatUsd(parsed) : "-";
        }

        static long UsageCacheAgeMs(string generatedAt)
        {
            if (string.IsNullOrWhiteSpace(generatedAt)) return -1;
            DateTimeOffset parsed;
            if (!DateTimeOffset.TryParse(generatedAt, out parsed)) return -1;
            return (long)Math.Max(0, (DateTimeOffset.Now - parsed.ToLocalTime()).TotalMilliseconds);
        }

        static string Trim(string text, int max)
        {
            if (string.IsNullOrEmpty(text) || text.Length <= max) return text ?? "";
            return text.Substring(0, Math.Max(0, max - 3)) + "...";
        }

        static string Pad(string text, int width)
        {
            text = text ?? "";
            return text.Length >= width ? text : text + new string(' ', width - text.Length);
        }
    }

    sealed class Card
    {
        public RoundedPanel Panel { get; private set; }
        public Label Value { get; private set; }
        readonly Label titleLabel;

        public Card(string title, int x, int y, int w, int h)
        {
            Panel = new RoundedPanel
            {
                Location = new Point(x, y),
                Size = new Size(w, h),
                BackColor = Color.White,
                BorderColor = Color.FromArgb(226, 232, 240),
                Radius = 16
            };
            titleLabel = new Label
            {
                Text = title,
                Location = new Point(12, 10),
                Size = new Size(w - 24, 22),
                ForeColor = Color.FromArgb(100, 116, 139),
                Font = new Font("Microsoft YaHei UI", 9f, FontStyle.Bold),
                BackColor = Color.Transparent
            };
            Value = new Label
            {
                Text = "-",
                Location = new Point(12, 38),
                Size = new Size(w - 24, h - 44),
                ForeColor = Color.FromArgb(15, 23, 42),
                Font = new Font("Microsoft YaHei UI", 15f, FontStyle.Bold),
                BackColor = Color.Transparent
            };
            Panel.Controls.Add(titleLabel);
            Panel.Controls.Add(Value);
        }

        public void SetBounds(int x, int y, int w, int h)
        {
            Panel.SetBounds(x, y, w, h);
            titleLabel.SetBounds(12, 10, Math.Max(20, w - 24), 22);
            Value.SetBounds(12, 38, Math.Max(20, w - 24), Math.Max(20, h - 44));
        }
    }

    sealed class InfoBadge : Control
    {
        public InfoBadge()
        {
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.UserPaint | ControlStyles.SupportsTransparentBackColor, true);
            BackColor = Color.Transparent;
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            var rect = new Rectangle(1, 1, Width - 3, Height - 3);
            using (var fill = new SolidBrush(Color.FromArgb(239, 246, 255)))
            using (var border = new Pen(Color.FromArgb(96, 165, 250)))
            using (var text = new SolidBrush(Color.FromArgb(37, 99, 235)))
            using (var font = new Font("Segoe UI", 7.5f, FontStyle.Bold))
            using (var format = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center })
            {
                e.Graphics.FillEllipse(fill, rect);
                e.Graphics.DrawEllipse(border, rect);
                e.Graphics.DrawString("i", font, text, rect, format);
            }
        }
    }

    sealed class SmoothDataGridView : DataGridView
    {
        public SmoothDataGridView()
        {
            DoubleBuffered = true;
        }
    }

    sealed class RoundedPanel : Panel
    {
        public int Radius { get; set; }
        public Color BorderColor { get; set; }

        public RoundedPanel()
        {
            Radius = 14;
            BorderColor = Color.FromArgb(226, 232, 240);
            DoubleBuffered = true;
            Padding = new Padding(1);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            using (var path = RoundedRect(new Rectangle(0, 0, Width - 1, Height - 1), Radius))
            using (var brush = new SolidBrush(BackColor))
            using (var pen = new Pen(BorderColor))
            {
                e.Graphics.FillPath(brush, path);
                e.Graphics.DrawPath(pen, path);
            }
        }

        static GraphicsPath RoundedRect(Rectangle bounds, int radius)
        {
            var diameter = radius * 2;
            var path = new GraphicsPath();
            path.AddArc(bounds.X, bounds.Y, diameter, diameter, 180, 90);
            path.AddArc(bounds.Right - diameter, bounds.Y, diameter, diameter, 270, 90);
            path.AddArc(bounds.Right - diameter, bounds.Bottom - diameter, diameter, diameter, 0, 90);
            path.AddArc(bounds.X, bounds.Bottom - diameter, diameter, diameter, 90, 90);
            path.CloseFigure();
            return path;
        }
    }

    static class EnumerableCompat
    {
        public static IEnumerable<T> TakeLastCompat<T>(this IEnumerable<T> source, int count)
        {
            var queue = new Queue<T>();
            foreach (var item in source)
            {
                queue.Enqueue(item);
                while (queue.Count > count) queue.Dequeue();
            }
            return queue.ToArray();
        }
    }
}
