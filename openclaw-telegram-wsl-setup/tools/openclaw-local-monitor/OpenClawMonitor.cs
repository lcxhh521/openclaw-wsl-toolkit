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

namespace OpenClawLocalMonitor
{
    static class Program
    {
        [STAThread]
        static void Main()
        {
            bool createdNew;
            using (var mutex = new System.Threading.Mutex(true, "Local\\OpenClawControlMonitor", out createdNew))
            {
                if (!createdNew) return;

                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new MonitorForm());
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
        public long TokenTotal;
        public long TokenInput;
        public long TokenOutput;
        public long TokenCacheRead;
        public string TokenContext = "-";
        public string CostText = "-";
        public string CostState = "warn";
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
        public readonly List<string> Logs = new List<string>();
        public readonly List<string> TokenFlows = new List<string>();
    }

    sealed class DiagnosticsSnapshot
    {
        public DateTime GeneratedAt = DateTime.Now;
        public string OverallState = "Unknown";
        public readonly List<string> OverallReasons = new List<string>();
        public readonly List<DiagnosticItem> Gateway = new List<DiagnosticItem>();
        public readonly List<DiagnosticItem> GatewayResilience = new List<DiagnosticItem>();
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

    sealed class MonitorForm : Form
    {
        const string WslDistro = "Ubuntu";
        const string OpenClawCommand = "openclaw";
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
        Button refreshButton;
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
        Button openControlButton;
        NotifyIcon trayIcon;
        ContextMenuStrip trayMenu;
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
            MinimumSize = new Size(1000, 700);
            ClientSize = new Size(1220, 760);
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
            timer.Interval = 30000;
            timer.Tick += async (s, e) => await RefreshAsync(false);
            timer.Start();
            clashTimer.Interval = 2500;
            clashTimer.Tick += async (s, e) => await EnsureClashSafeModeAsync(false);
            clashTimer.Start();
            Shown += async (s, e) =>
            {
                LayoutUi();
                Invalidate(true);
                Update();
                await EnsureClashSafeModeAsync(true);
                await RefreshAsync(false);
            };
            FormClosing += OnFormClosing;
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
            trayMenu.Items.Add("重新检测", null, async (s, e) =>
            {
                ShowFromTray();
                await RefreshAsync(true);
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
            BeginSmoothRestore();
            ShowInTaskbar = true;
            WindowState = FormWindowState.Normal;
            Show();
            FinishSmoothRestore(true);
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
            Opacity = 0;
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
                if (activate) Activate();
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
                Text = "打开 Control",
                Location = new Point(962, 20),
                Size = new Size(112, 36),
                BackColor = Color.FromArgb(15, 23, 42),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat
            };
            openControlButton.FlatAppearance.BorderSize = 0;
            openControlButton.Click += (s, e) => OpenControl();
            AddBoundedHoverTip(openControlButton, "打开浏览器版 Control。");
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

            refreshButton = new Button
            {
                Text = "重新检测",
                Location = new Point(1090, 20),
                Size = new Size(92, 36),
                BackColor = Color.FromArgb(37, 99, 235),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat
            };
            refreshButton.FlatAppearance.BorderSize = 0;
            refreshButton.Click += async (s, e) => await RefreshAsync(true);
            AddBoundedHoverTip(refreshButton, "只读重查状态；不启动 gateway、不改配置、不重置任务。");
            Controls.Add(refreshButton);

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
            tasks = new Card("后台任务", 604, 232, 176, 88);
            audit = new Card("提醒", 796, 232, 176, 88);
            session = new Card("最近活动", 988, 232, 194, 88);
            Controls.AddRange(new Control[] { overall.Panel, gateway.Panel, telegram.Panel, tasks.Panel, audit.Panel, session.Panel });

            Controls.Add(MakeLabel("Token / 成本流向", 28, 344, 260, 24, 12f, Color.FromArgb(15, 23, 42), true));
            tokenTotal = new Card("上下文占用", 28, 376, 142, 84);
            tokenInput = new Card("输入 Token", 184, 376, 142, 84);
            tokenOutput = new Card("输出 Token", 340, 376, 142, 84);
            tokenCache = new Card("缓存读取", 496, 376, 142, 84);
            tokenCost = new Card("已记录成本", 652, 376, 128, 84);
            Controls.AddRange(new Control[] { tokenTotal.Panel, tokenInput.Panel, tokenOutput.Panel, tokenCache.Panel, tokenCost.Panel });
            AddCostHint();

            Controls.Add(MakeLabel("后台任务状态", 28, 486, 260, 24, 12f, Color.FromArgb(15, 23, 42), true));
            taskGrid = new SmoothDataGridView
            {
                Location = new Point(28, 516),
                Size = new Size(1154, 150),
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

            Controls.Add(MakeLabel("最近会话", 28, 692, 240, 24, 12f, Color.FromArgb(15, 23, 42), true));
            sessionList = MakeList(28, 722, 560, 120);
            Controls.Add(sessionList);

            Controls.Add(MakeLabel("最近提醒", 622, 692, 330, 24, 12f, Color.FromArgb(15, 23, 42), true));
            logList = MakeList(622, 722, 560, 120);
            Controls.Add(logList);

            statusLine = MakeLabel("", 28, 852, 1154, 24, 9f, Color.FromArgb(100, 116, 139), false);
            Controls.Add(statusLine);
            legendLine = MakeLabel("绿色=就绪，蓝色=正在工作，黄色=需要留意，红色=需要处理。", 28, 874, 1154, 22, 8.5f, Color.FromArgb(148, 163, 184), false);
            Controls.Add(legendLine);
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
                Location = new Point(10, 8),
                Size = new Size(160, 18),
                AutoEllipsis = true,
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

        void ShowBoundedHoverTip(Control target, string text)
        {
            if (hoverTip == null || hoverTipText == null) return;
            hoverTipText.Text = text;
            var measured = TextRenderer.MeasureText(text, hoverTipText.Font);
            var width = Math.Min(Math.Max(measured.Width + 24, 150), Math.Max(150, ClientSize.Width - 56));
            var height = 34;
            var screenPoint = target.PointToScreen(new Point(0, target.Height + 8));
            var local = PointToClient(screenPoint);
            var x = Math.Max(28, Math.Min(local.X, ClientSize.Width - width - 28));
            var y = local.Y;
            if (y + height > ClientSize.Height - 28)
                y = PointToClient(target.PointToScreen(new Point(0, -height - 8))).Y;
            y = Math.Max(28, y);
            hoverTip.SetBounds(x, y, width, height);
            hoverTipText.SetBounds(10, 8, width - 20, 18);
            hoverTip.Visible = true;
            hoverTip.BringToFront();
        }

        void HideBoundedHoverTip()
        {
            if (hoverTip != null) hoverTip.Visible = false;
        }

        void LayoutUi()
        {
            if (refreshButton == null || taskGrid == null) return;
            SuspendLayout();
            try
            {
                var compact = ClientSize.Width < 1180;
                var margin = compact ? 18 : 28;
                var gap = compact ? 10 : 16;
                var refreshWidth = compact ? 86 : 92;
                var diagnosticsWidth = compact ? 68 : 72;
                var openControlWidth = compact ? 104 : 112;
                var openClawPowerWidth = compact ? 122 : 130;
                var contentWidth = Math.Max(760, ClientSize.Width - margin * 2);
                var clientHeight = Math.Max(680, ClientSize.Height);

                refreshButton.SetBounds(margin + contentWidth - refreshWidth, 20, refreshWidth, 36);
                diagnosticsButton.SetBounds(refreshButton.Left - gap - diagnosticsWidth, 20, diagnosticsWidth, 36);
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
                    hero.SetBounds(margin, 84 + topExtra, contentWidth, 104);
                    heroTitle.SetBounds(28, 16, Math.Max(420, contentWidth - 56), 34);
                    heroDetail.SetBounds(30, 54, Math.Max(420, contentWidth - 60), 24);
                    if (startupProgressPanel != null)
                    {
                        var progressWidth = Math.Max(420, contentWidth - 60);
                        startupProgressPanel.SetBounds(30, 78, progressWidth, 20);
                        startupProgressText.SetBounds(12, 1, Math.Max(180, progressWidth - 420), 18);
                        startupProgressBar.SetBounds(Math.Max(220, progressWidth - 700), 4, Math.Min(680, progressWidth - 240), 12);
                    }
                }

                var topCards = new[] { overall, gateway, telegram, tasks, audit, session };
                var topColumns = contentWidth >= 1060 ? 6 : 3;
                var topCardWidth = (contentWidth - gap * (topColumns - 1)) / topColumns;
                var y = 206 + topExtra;
                for (var i = 0; i < topCards.Length; i++)
                {
                    var row = i / topColumns;
                    var col = i % topColumns;
                    topCards[i].SetBounds(margin + col * (topCardWidth + gap), y + row * 104, topCardWidth, 88);
                }
                y += ((topCards.Length + topColumns - 1) / topColumns) * 104 + 8;

                MoveDirectLabelFromOriginalY(344, margin, y, 260, 24);
                y += 32;
                var tokenCards = new[] { tokenTotal, tokenInput, tokenOutput, tokenCache, tokenCost };
                var tokenGap = contentWidth >= 1120 ? gap : 12;
                var tokenCardWidth = (contentWidth - tokenGap * (tokenCards.Length - 1)) / tokenCards.Length;
                for (var i = 0; i < tokenCards.Length; i++)
                    tokenCards[i].SetBounds(margin + i * (tokenCardWidth + tokenGap), y, tokenCardWidth, 84);
                y += 110;

                if (costHintPopup != null)
                {
                    var hintWidth = Math.Min(530, contentWidth);
                    costHintPopup.SetBounds(Math.Min(tokenCost.Panel.Left, margin + contentWidth - hintWidth), tokenCost.Panel.Bottom + 8, hintWidth, 56);
                }

                MoveDirectLabelFromOriginalY(486, margin, y, 260, 24);
                y += 30;
                var statusArea = 54;
                var listHeaderAndGap = 32;
                var gridToListsGap = 32;
                var availableLower = Math.Max(300, clientHeight - y - statusArea - listHeaderAndGap - gridToListsGap);
                var gridHeight = Math.Max(126, Math.Min(220, (int)(availableLower * 0.54)));
                var listHeight = Math.Max(126, availableLower - gridHeight);
                taskGrid.SetBounds(margin, y, contentWidth, gridHeight);
                y += gridHeight + gridToListsGap;

                var halfWidth = (contentWidth - gap) / 2;
                MoveDirectLabelFromOriginalY(692, margin, y, halfWidth, 24);
                MoveDirectLabelFromOriginalX(622, margin + halfWidth + gap, y, halfWidth, 24);
                sessionList.SetBounds(margin, y + 30, halfWidth, listHeight);
                logList.SetBounds(margin + halfWidth + gap, y + 30, halfWidth, listHeight);
                y += listHeight + listHeaderAndGap;

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

        async Task RefreshAsync(bool manualRecovery)
        {
            if (refreshing) return;
            refreshing = true;
            if (manualRecovery)
                updated.Text = "重新检测中...";
            else if (togglingOpenClaw)
                updated.Text = lastOpenClawServiceActive ? "关闭中..." : "启动中...";
            refreshButton.Enabled = false;
            try
            {
                var snapshot = await Task.Run(() => BuildSnapshot(manualRecovery));
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
                refreshButton.Enabled = true;
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
            FillDiagnosticsTelegram(d);
            FillDiagnosticsSessions(d);
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
                    foreach (var line in stability.Item2.Split(new[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries).Take(5))
                    {
                        var columns = line.Split(new[] { '\t' }, 2);
                        var modified = columns.Length > 0 ? columns[0] : "-";
                        var name = columns.Length > 1 ? columns[1] : line;
                        var match = Regex.Match(name, @"^openclaw-stability-(.+)-(\d+)-(.+)\.json$", RegexOptions.IgnoreCase);
                        var timestamp = match.Success ? match.Groups[1].Value : modified;
                        var pid = match.Success ? match.Groups[2].Value : "-";
                        var reason = match.Success ? match.Groups[3].Value : name;
                        var state = Regex.IsMatch(reason, "stop_shutdown_timeout|SIGKILL|killed|startup_failed", RegexOptions.IgnoreCase) ? "Risk" :
                            Regex.IsMatch(reason, "restart|SIGTERM|timeout", RegexOptions.IgnoreCase) ? "Warn" : "Good";
                        d.GatewayResilience.Add(new DiagnosticItem("Stability file", reason + " · pid " + pid, state, timestamp + " · " + name, "stability files"));
                        count++;
                    }
                    if (count == 0) d.GatewayResilience.Add(new DiagnosticItem("Stability files", "无记录", "Good", "未发现 stability json", "stability files"));
                }
                else if (stability.Item1)
                {
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
                    var ok = configured && running && connected;
                    d.Telegram.Add(new DiagnosticItem("Channel", ok ? "已连接" : "需检查", ok ? "Good" : "Risk", "configured=" + configured + ", running=" + running + ", connected=" + connected, "channels status --json"));
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
            var all = d.Gateway.Concat(d.GatewayResilience).Concat(d.Telegram).Concat(d.Sessions).Concat(d.TasksLogs).ToList();
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
                "[ -d ~/.openclaw/logs/stability ] || exit 0\n" +
                "ls -1t ~/.openclaw/logs/stability/*.json 2>/dev/null | head -5 | sed 's#.*/##'";
            var result = RunProcess("wsl.exe", new[] { "-d", WslDistro, "--", "bash", "-lc", script }, 3000);
            return Tuple.Create(result.Ok, result.Stdout, result.Stderr + result.Error);
        }

        Tuple<bool, string, string> GetOpenClawTasksResidualProcessesReadonly()
        {
            var script = "ps -eo pid=,ppid=,pcpu=,pmem=,rss=,etime=,args= 2>/dev/null | grep -E '[o]penclaw tasks|[o]penclaw-tasks' | head -8";
            var result = RunProcess("wsl.exe", new[] { "-d", WslDistro, "--", "bash", "-lc", script }, 5000);
            if (!result.Ok && string.IsNullOrWhiteSpace(result.Stdout)) return Tuple.Create(true, "", "");
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
            AppendDiagnosticsSection(sb, "Telegram", d.Telegram);
            AppendDiagnosticsSection(sb, "Sessions", d.Sessions);
            AppendDiagnosticsSection(sb, "Tasks & Logs", d.TasksLogs);
            sb.AppendLine();
            sb.AppendLine("v0 边界：只读；不自动重启、不 maintenance --apply、不清理 session、不改 binding/model/secrets、不写 memory。");
            return RedactSensitive(sb.ToString());
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
                    : (shouldStop ? "已尝试关闭 OpenClaw；如果仍显示运行，请稍后重新检测。" : "已尝试启动 OpenClaw；如果仍异常，请查看状态卡片。");
            }
            finally
            {
                togglingOpenClaw = false;
                UpdateOpenClawPowerUi();
            }
            await RefreshAsync(false);
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
            var script =
                "systemctl --user start openclaw-gateway.service >/dev/null 2>&1 || true\n" +
                "pgrep -af 'openclaw-manual-keepalive' >/dev/null 2>&1 || (nohup bash -lc 'exec -a openclaw-manual-keepalive sleep infinity' >/dev/null 2>&1 &)\n" +
                "for i in $(seq 1 45); do openclaw gateway probe >/dev/null 2>&1 && exit 0; sleep 1; done\n" +
                "exit 1";
            return RunProcess("wsl.exe", new[] { "-d", WslDistro, "--", "bash", "-lc", script }, 60000);
        }

        CommandResult StopOpenClawGateway()
        {
            var script =
                "systemctl --user stop openclaw-gateway.service >/dev/null 2>&1 || true\n" +
                "pkill -f '[o]penclaw-manual-keepalive' >/dev/null 2>&1 || true\n" +
                "for i in $(seq 1 20); do openclaw gateway probe >/dev/null 2>&1 || exit 0; sleep 1; done\n" +
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

        Snapshot BuildSnapshot(bool manualRecovery)
        {
            var probeTask = Task.Run(() => RunOpenClawJson(new[] { "gateway", "probe", "--json", "--timeout", "8000" }, 12000));
            var channelStatusTask = Task.Run(() => RunOpenClawJson(new[] { "channels", "status", "--json", "--timeout", "8000" }, 10000));
            Task.WaitAll(probeTask, channelStatusTask);

            var probe = probeTask.Result;
            var channelStatus = channelStatusTask.Result;
            var openClawServiceActive = GatewayServiceLooksActive();

            var snapshot = new Snapshot();
            snapshot.OpenClawServiceActive = probe.Item1 || channelStatus.Item1 || openClawServiceActive;
            SetStartupProgress(snapshot, 0, "等待开启", "OpenClaw 未启动或正在等待检测。");
            if (!probe.Item1)
            {
                gatewayProbeFailures++;
                snapshot.Error = probe.Item3;
                var serviceLooksAlive = channelStatus.Item1 || openClawServiceActive;
                if (serviceLooksAlive && gatewayProbeFailures < 3)
                {
                    snapshot.State = "Degraded";
                    snapshot.GatewayText = "启动中";
                    snapshot.GatewaySoftFailure = true;
                    SetStartupProgress(snapshot, 35, "网关启动中", "OpenClaw 服务有响应，正在等待 gateway 稳定。");
                    snapshot.StatusLine = "OpenClaw 服务仍有响应；面板会继续等待启动链路稳定。";
                }
                else
                {
                    snapshot.State = "Problem";
                    snapshot.GatewayText = "未连接";
                    SetStartupProgress(snapshot, 0, "网关未响应", "OpenClaw 尚未进入可用启动链路。");
                    snapshot.StatusLine = string.IsNullOrWhiteSpace(probe.Item3) ? "OpenClaw 启动链路尚未连通。" : probe.Item3;
                }
            }
            else
            {
                gatewayProbeFailures = 0;
                FillFromProbe(snapshot, probe.Item2);
            }
            FillChannelStatus(snapshot, channelStatus.Item2);

            if (ShouldUseStartupLightProbe(snapshot))
            {
                FillStartupLightPlaceholders(snapshot);
                if (!string.IsNullOrWhiteSpace(startupNote) && snapshot.GatewayOk)
                    snapshot.StatusLine = startupNote + " | " + snapshot.StatusLine;
                return snapshot;
            }

            if (!manualRecovery)
            {
                FillSteadyLightPlaceholders(snapshot);
                FillConversationActivity(snapshot);
                FillTaskTableFallback(snapshot);
                if (!string.IsNullOrWhiteSpace(startupNote) && snapshot.GatewayOk)
                    snapshot.StatusLine = startupNote + " | " + snapshot.StatusLine;
                return snapshot;
            }

            var statusTask = Task.Run(() => RunOpenClawJson(new[] { "status", "--json" }, 15000));
            var tasksTask = Task.Run(() => RunOpenClawJson(new[] { "tasks", "list", "--json" }, 12000));
            var workspaceTask = Task.Run(() => RunWorkspaceActivity());
            Task.WaitAll(statusTask, tasksTask, workspaceTask);

            var status = statusTask.Result;
            var taskData = tasksTask.Result;
            var workspaceActivity = workspaceTask.Result;

            FillTokenUsage(snapshot, status.Item2);
            FillTasks(snapshot, taskData.Item2);
            FillWorkspaceActivity(snapshot, workspaceActivity);
            snapshot.CostText = "\u5df2\u8df3\u8fc7";
            snapshot.CostState = "warn";
            snapshot.Logs.Add("主面板已降级：重新检测不再读取 tasks audit、TaskFlow、logs.tail 或成本扫描，避免拖慢 Telegram。");
            snapshot.TokenFlows.Add("成本 · 已跳过本轮扫描；需要成本细节时再单独检查。");
            FillConversationActivity(snapshot);
            FillTaskTableFallback(snapshot);

            if ((snapshot.RunningTasks > 0 || snapshot.FlowActive > 0 || snapshot.FlowBlocked > 0 || snapshot.FlowCancelRequested > 0 || snapshot.LocalWorkItems > 0) && snapshot.State != "Problem" && snapshot.State != "Degraded")
                snapshot.State = "Working";

            snapshot.StatusLine = string.IsNullOrWhiteSpace(snapshot.StatusLine)
                ? snapshot.GatewayText
                : snapshot.StatusLine;
            if (!string.IsNullOrWhiteSpace(startupNote) && snapshot.GatewayOk)
                snapshot.StatusLine = startupNote + " | " + snapshot.StatusLine;
            return snapshot;
        }

        bool ShouldUseStartupLightProbe(Snapshot snapshot)
        {
            if (!snapshot.GatewayOk) return true;
            if (!snapshot.TelegramOk) return true;
            return snapshot.StartupProgress > 0 && snapshot.StartupProgress < 100;
        }

        void FillStartupLightPlaceholders(Snapshot snapshot)
        {
            if (snapshot.GatewayOk)
            {
                snapshot.Tasks.Add(new[] { "\u542f\u52a8\u68c0\u67e5", "\u8f7b\u91cf\u63a2\u6d4b", "\u542f\u52a8\u4e2d", "-", "\u6682\u4e0d\u8bfb\u53d6\u91cd\u4efb\u52a1\uff0c\u7b49\u5f85 gateway / Telegram \u7a33\u5b9a" });
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
            snapshot.Tasks.Add(new[] { "\u63a7\u5236\u4e2d\u5fc3\u81ea\u52a8\u5237\u65b0", "\u8f7b\u91cf\u63a2\u6d4b", "\u5df2\u964d\u7ea7", "-", "\u81ea\u52a8\u5237\u65b0\u53ea\u8bfb\u53d6 gateway probe \u548c Telegram channel\uff0c\u907f\u514d\u62a2\u5360 Telegram \u5165\u53e3" });
            snapshot.Sessions.Add("\u81ea\u52a8\u5237\u65b0\u5df2\u964d\u7ea7\uff1a\u4e0d\u8bfb\u53d6 24h sessions / high-token \u5217\u8868\u3002\u9700\u8981\u65f6\u70b9\u201c\u8bca\u65ad\u201d\u3002");
            snapshot.Logs.Add("\u81ea\u52a8\u5237\u65b0\u5df2\u964d\u7ea7\uff1a\u4e0d\u8bfb\u53d6 logs.tail / tasks audit / TaskFlow / \u6210\u672c\u626b\u63cf\u3002");
            snapshot.TokenFlows.Add("\u81ea\u52a8\u5237\u65b0\u4e0d\u8bfb\u53d6 Token/\u6210\u672c\u5feb\u7167\uff0c\u907f\u514d\u89e6\u53d1\u91cd RPC\u3002\u9700\u8981\u7ec6\u8282\u65f6\u70b9\u201c\u91cd\u65b0\u68c0\u6d4b\u201d\u6216\u201c\u8bca\u65ad\u201d\u3002");
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

            s.TelegramOk = true;
            s.TelegramText = "已连接";
            s.TelegramCardState = "good";

            if (startupWindow)
            {
                var warmupProgress = 85 + (int)Math.Min(14, Math.Max(0, startAgeMs / 9000));
                SetStartupProgress(s, warmupProgress, "冷启动预热", "Telegram 已连接，等待模型、sidecar 和通道稳定：" + AgeSince(s.TelegramLastStartAt) + "。");
                if (s.State == "Problem") s.State = "Degraded";
                if (string.IsNullOrWhiteSpace(s.StatusLine) || s.StatusLine.Contains("Telegram 已连接"))
                    s.StatusLine = "Telegram 已连接；OpenClaw 刚启动 " + AgeSince(s.TelegramLastStartAt) + "，模型和 sidecar 可能仍在预热。";
                return;
            }

            var verified = s.TelegramLastOutboundAt > 0 && (s.TelegramLastStartAt <= 0 || s.TelegramLastOutboundAt >= s.TelegramLastStartAt);
            SetStartupProgress(s, 100, verified ? "回复链路已验证" : "已就绪", verified ? "本次启动后已有 Telegram 回复记录。" : "gateway 和 Telegram 已稳定，冷启动窗口已结束。");
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
            var parts = new List<string> { OpenClawCommand };
            parts.AddRange(args.Select(ShellQuote));
            return string.Join(" ", parts);
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
            telegram.Value.Text = s.TelegramText;
            SetCard(telegram, s.TelegramCardState);
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

            taskGrid.Rows.Clear();
            foreach (var row in s.Tasks) taskGrid.Rows.Add(row);

            sessionList.Items.Clear();
            foreach (var row in s.Sessions) sessionList.Items.Add(row);
            if (s.Sessions.Count == 0) sessionList.Items.Add("暂未读取到会话数据。");

            logList.Items.Clear();
            foreach (var row in s.Logs) logList.Items.Add(row);

            statusLine.Text = s.StatusLine;
        }

        string HeroTitle(Snapshot s)
        {
            if (s.State == "Problem") return "需要处理";
            if (s.State == "Degraded") return "OpenClaw 启动中";
            if (s.State == "Working") return "OpenClaw 正在工作";
            if (s.State == "Ready") return "OpenClaw 已就绪";
            return "OpenClaw 当前安静";
        }

        string HeroDetail(Snapshot s)
        {
            if (s.State == "Problem")
            {
                if (!s.GatewayOk) return "控制中心连不上网关。请检查 WSL 或 OpenClaw gateway。";
                if (!s.TelegramOk) return "网关可连接，但 Telegram 未连接或未配置。";
                if (s.AuditErrors > 0) return "任务审计有错误。请查看提醒和日志。";
                return "有项目需要处理。";
            }
            if (s.State == "Degraded") return "OpenClaw 服务仍有响应，正在等待 gateway 和 Telegram 启动链路稳定。";
            if (s.State == "Working") return "检测到 OpenClaw 注册任务、活跃 TaskFlow、仍在运行的本地 daemon，或连续刷新之间的新产物写入。可以在下方表格看进展。";
            if (s.State == "Ready") return "网关和 Telegram 已连接；后台没有 queued/running 任务、活跃 TaskFlow 或仍在运行的本地 daemon。";
            return "后台没有 queued/running 任务、活跃 TaskFlow 或仍在运行的本地 daemon。";
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
            if (state == "Problem") return "需要处理";
            if (state == "Degraded") return "需观察";
            if (state == "Working") return "正在工作";
            if (state == "Ready") return "就绪";
            return "空闲";
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
            const string text = "这是 OpenClaw 根据本月本地 usage.cost 记录汇总的估算成本，每月 1 号自然重新开始。它不等同于服务商最终账单，实际扣费以 OpenAI / Gemini / DeepSeek 等后台账单为准。";
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
                Size = new Size(530, 56),
                BackColor = Color.FromArgb(248, 250, 252),
                BorderColor = Color.FromArgb(203, 213, 225),
                Radius = 12,
                Visible = false
            };
            var hintText = new Label
            {
                Text = text,
                Location = new Point(14, 9),
                Size = new Size(502, 38),
                AutoEllipsis = false,
                ForeColor = Color.FromArgb(51, 65, 85),
                Font = new Font("Microsoft YaHei UI", 9f),
                BackColor = Color.Transparent
            };
            costHintPopup.Controls.Add(hintText);
            Controls.Add(costHintPopup);

            EventHandler show = (s, e) =>
            {
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
