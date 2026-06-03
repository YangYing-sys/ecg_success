import sys
import re
import time
import threading
import serial
import serial.tools.list_ports
from collections import deque

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.widget import Widget
from kivy.graphics import Line, Color, Rectangle, InstructionGroup
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.utils import get_color_from_hex
from kivy.utils import platform
from kivy.uix.popup import Popup
# -----csv-----
import os
import csv
from datetime import datetime, timedelta


class CSVDataManager:
    def __init__(self):
        self.last_save_time = 0
        self.save_folder = self.get_android_public_folder()
        self.clean_7days_old_files()  # 启动时清理7天前的垃圾文件

    def save_waveform(self, clean_ecg_value):
        """实时记录‘干净’的波形数值到 CSV"""
        filepath = os.path.join(self.save_folder, f"ECG_Waveform_{datetime.now().strftime('%Y-%m-%d')}.csv")
        file_exists = os.path.isfile(filepath)

        try:
            with open(filepath, mode='a', newline='') as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(['Time', 'Voltage'])  # 表头

                # 记录时间戳和滤波后的值
                time_str = datetime.now().strftime('%H:%M:%S.%f')[:-3]
                writer.writerow([time_str, clean_ecg_value])
        except:
            pass

    def get_android_public_folder(self):
        """获取手机存储，将文件存放到手机的 ‘Download / 心电数据’ 文件夹内"""
        if platform == 'android':
            # 请求安卓读写权限
            from android.permissions import request_permissions, Permission
            request_permissions([Permission.WRITE_EXTERNAL_STORAGE, Permission.READ_EXTERNAL_STORAGE])

            # 使用 jnius 调用安卓底层 API 获取 Download 文件夹
            from jnius import autoclass
            Environment = autoclass('android.os.Environment')
            base_path = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS).getAbsolutePath()
            folder_path = os.path.join(base_path, '心电数据记录')
        else:
            # 如果在电脑上运行，就存在代码旁边的文件夹里
            folder_path = os.path.join(os.getcwd(), '心电数据记录')

        # 如果文件夹不存在就创建
        if not os.path.exists(folder_path):
            os.makedirs(folder_path, exist_ok=True)

        return folder_path

    def get_today_filename(self):
        """获取今天专属的 CSV 文件路径"""
        today_str = datetime.now().strftime('%Y-%m-%d')
        filename = f"ECG_Log_{today_str}.csv"
        return os.path.join(self.save_folder, filename)

    def save_data(self, bpm, hrv, rhythm):
        """保存数据，依然做 5 秒限流保护手机运存"""
        current_time = time.time()
        # 限流：如果是正常心律，每5秒存一次；如果是异常（AFib等），立刻存！
        if current_time - self.last_save_time < 5.0 and rhythm == "Normal":
            return

        self.last_save_time = current_time
        filepath = self.get_today_filename()

        # 检查文件是否是新创建的，如果是就需要写表头
        file_exists = os.path.isfile(filepath)

        try:
            # mode='a' 表示在文件末尾追加 (Append)
            with open(filepath, mode='a', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                if not file_exists:
                    # 写入表头 (utf-8-sig 保证 Excel 打开不会乱码！)
                    writer.writerow(['记录时间', '心率 (BPM)', 'RR波动差/HRV (ms)', '心律状态'])

                time_str = datetime.now().strftime('%H:%M:%S')
                writer.writerow([time_str, bpm, hrv, rhythm])
        except Exception as e:
            print("CSV 保存失败:", e)

    def clean_7days_old_files(self):
        """扫描文件夹，删除 7 天前的文件"""
        try:
            now = datetime.now()
            for filename in os.listdir(self.save_folder):
                if filename.startswith("ECG_Log_") and filename.endswith(".csv"):
                    # 从文件名中提取出日期 (比如 ECG_Log_2026-04-05.csv -> 2026-04-05)
                    date_str = filename.replace("ECG_Log_", "").replace(".csv", "")
                    try:
                        file_date = datetime.strptime(date_str, '%Y-%m-%d')
                        # 如果文件的日期距离今天超过 7 天
                        if (now - file_date).days > 7:
                            file_to_del = os.path.join(self.save_folder, filename)
                            os.remove(file_to_del)
                            print(f"已清理过期文件: {filename}")
                    except ValueError:
                        pass
        except Exception as e:
            pass


# === 全局样式 ===
FONT_NAME = 'simhei.ttf'
EMOJI_FONT = 'seguiemj.ttf'
Window.clearcolor = get_color_from_hex('#F9F9F9')


def E(emoji_char):
    return f"[font={EMOJI_FONT}]{emoji_char}[/font]"


class RichLogBox(Label):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.markup = True
        self.halign = 'left'
        self.valign = 'top'
        self.font_name = FONT_NAME
        self.font_size = '16sp'
        self.color = get_color_from_hex('#333333')

        with self.canvas.before:
            Color(1, 1, 1, 1)
            self.bg = Rectangle(pos=self.pos, size=self.size)
            Color(0.8, 0.8, 0.8, 1)
            self.border = Line(rectangle=(self.x, self.y, self.width, self.height), width=1)

        self.bind(pos=self.update_bg, size=self.update_bg)

    def update_bg(self, *args):
        self.bg.pos = self.pos
        self.bg.size = self.size
        self.border.rectangle = (self.x, self.y, self.width, self.height)
        self.text_size = (self.width - 30, self.height - 30)


# ==========================================
# 1. 硬件线程 (包含安卓HC-05蓝牙原生支持)
# ==========================================
class HardwareThread(threading.Thread):
    def __init__(self, data_callback, status_callback):
        super().__init__()
        self.data_callback = data_callback
        self.status_callback = status_callback
        self.running = True

    def run(self):
        if platform == 'android':
            self.run_bluetooth_mode()
        else:
            self.run_serial_mode()

    def run_bluetooth_mode(self):
        """ 专门给手机 APP 连蓝牙模块（HC-05）的方法 """
        self.status_callback("【蓝牙寻找】寻找配对的 HC-05 设备...")

        # =================【关键修复：附着 JVM 线程】=================
        try:
            import jnius
            jnius.attach_thread()
        except Exception as e:
            self.status_callback(f"【错误】无法附着 JVM 线程: {str(e)}")
            return

        try:
            from jnius import autoclass
            BluetoothAdapter = autoclass('android.bluetooth.BluetoothAdapter')
            UUID = autoclass('java.util.UUID')
            adapter = BluetoothAdapter.getDefaultAdapter()

            if adapter is None or not adapter.isEnabled():
                self.status_callback("【错误】蓝牙未开启，请先在手机设置中打开蓝牙！")
                return

            paired_devices = adapter.getBondedDevices().toArray()
            hc05_device = None

            # 优先寻找名字里带 HC-05 的设备
            for device in paired_devices:
                name = device.getName()
                if name and ("HC-05" in name or "HC05" in name or "JDY" in name):
                    hc05_device = device
                    break

            # 如果没找到带特定名字的，自动回退到第一个绑定的设备
            if not hc05_device and len(paired_devices) > 0:
                hc05_device = paired_devices[0]

            if not hc05_device:
                self.status_callback("【错误】未找到已配对的蓝牙设备，请前往手机设置配对！")
                return

            self.status_callback(f"【蓝牙】正在连接: {hc05_device.getName()}...")
            # 蓝牙串口服务标准 UUID (SPP)
            uuid = UUID.fromString("00001101-0000-1000-8000-00805F9B34FB")
            socket = hc05_device.createRfcommSocketToServiceRecord(uuid)
            socket.connect()

            input_stream = socket.getInputStream()
            self.status_callback("【硬件链路】HC-05蓝牙连接成功！开始接收数据...")

            buffer = ""
            while self.running:
                # 阻塞读取输入流，CPU消耗极低
                byte_val = input_stream.read()
                if byte_val == -1:
                    break  # 流断开了

                ch = chr(byte_val)
                buffer += ch

                if ch == '\n':
                    self.parse_and_emit(buffer.strip())
                    buffer = ""

        except Exception as e:
            self.status_callback(f"【蓝牙链路中断】: {str(e)}")
        # =================【关键修复：处理结束后释放 JVM 资源】=================
        finally:
            try:
                import jnius
                jnius.detach_thread()
            except:
                pass

    def run_serial_mode(self):
        self.status_callback("【探测中】正在寻找 USB 链路...")
        ports = list(serial.tools.list_ports.comports())
        if not ports:
            self.status_callback("【错误】未检测到蓝牙设备，请检查是否已连接！")  # 【错误】未检测到串口设备，请检查 CH340 驱动！
            return

        port_name = ports[0].device
        try:
            # 提高串口读取的鲁棒性
            ser = serial.Serial(port_name, 115200, timeout=0.05)
            # 开局先清空一次缓冲区
            ser.reset_input_buffer()

            while self.running:
                # 贪婪读取机制：如果缓冲区堆积了太多数据，瞬间丢弃旧数据，直接抓最新的！
                if ser.in_waiting > 500:
                    ser.reset_input_buffer()

                line_bytes = ser.readline()
                if line_bytes:
                    try:
                        line = line_bytes.decode('utf-8', errors='ignore').strip()
                        if line:
                            self.parse_and_emit(line)
                    except:
                        continue
        except Exception as e:
            self.status_callback(f"【链路中断】: {str(e)}")

    def parse_and_emit(self, line):
        """ 解析新的单片机多协议格式： ADC:4026 | ECG:67.4 | BPM:0 | RR:0 | HRV:0 | PVC """
        try:
            # 1. 正则提取
            m_ecg = re.search(r'ECG:([-+]?\d*\.?\d+)', line)
            m_bpm = re.search(r'BPM:(\d+)', line)
            m_hrv = re.search(r'HRV:(\d+)', line)

            # 2. 提取数值
            ecg_val = float(m_ecg.group(1)) if m_ecg else 0.0
            bpm_val = int(m_bpm.group(1)) if m_bpm else 0
            hrv_val = int(m_hrv.group(1)) if m_hrv else 0

            # 3. 实时状态判定 (支持新版 C 代码协议的尾部字符串)
            rhythm_str = "Wait"
            if "Normal" in line:
                rhythm_str = "Normal"
            elif "AFib" in line:
                rhythm_str = "AFib"
            elif "PVC" in line:
                rhythm_str = "PVC"
            elif "Wait" in line or "Invalid" in line:
                rhythm_str = "Wait"

            # 4. 同步推送机制向 UI 通知更新
            self.data_callback(ecg_val, bpm_val, hrv_val, rhythm_str)

            # 5. 只有真正有数值时才录入 CSV，防止日志被 Wait 刷屏
            if bpm_val > 0 and rhythm_str != "Wait":
                app = App.get_running_app()
                if hasattr(app, 'csv_manager'):
                    app.csv_manager.save_data(bpm_val, hrv_val, rhythm_str)

        except Exception as e:
            # 静默处理通讯过程中的残缺行
            pass

    def stop(self):
        self.running = False


# ==========================================
# 2. 精密 ECG 绘图 Widget (已彻底移除 Numpy)
# ==========================================
from kivy.uix.floatlayout import FloatLayout


class ECGPlotWidget(FloatLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.data_len = 1000
        # =================【关键修复：使用纯 Python List 替代 numpy 数组】=================
        self.ecg_buffer = [0.0] * self.data_len
        self.ptr = 0
        self.display_mode = 'FLAT'

        self.baseline = 0.0
        self.smooth_window = []
        self.window_size = 15

        with self.canvas.before:
            Color(1, 1, 1, 1)
            self.bg = Rectangle()
            self.grid_lines = InstructionGroup()
            self.canvas.before.add(self.grid_lines)

        with self.canvas:
            Color(*get_color_from_hex('#e74c3c'))
            self.line = Line(points=[], width=1.2)

        self.bind(pos=self.update_canvas, size=self.update_canvas)

        self.y_labels = []
        for y_val in [60, 40, 20, 0, -20, -40, -60]:
            lbl = Label(text=str(y_val), color=get_color_from_hex('#999999'),
                        font_size='12sp', size_hint=(None, None), size=(35, 20))
            self.add_widget(lbl)
            self.y_labels.append((lbl, y_val))

        self.x_labels = []
        for x_val in [0, 200, 400, 600, 800, 1000]:
            lbl = Label(text=str(x_val), color=get_color_from_hex('#999999'),
                        font_size='12sp', size_hint=(None, None), size=(40, 20))
            self.add_widget(lbl)
            self.x_labels.append((lbl, x_val))

        Clock.schedule_interval(self.render, 1.0 / 60.0)

    def update_canvas(self, *args):
        pad_left = 40
        pad_bottom = 25
        pad_right = 10
        pad_top = 10

        plot_x = self.x + pad_left
        plot_y = self.y + pad_bottom
        plot_w = self.width - pad_left - pad_right
        plot_h = self.height - pad_bottom - pad_top

        self.bg.pos = (plot_x, plot_y)
        self.bg.size = (plot_w, plot_h)
        self.grid_lines.clear()

        y_range = 120.0
        for lbl, y_val in self.y_labels:
            norm_y = (y_val + 60) / y_range
            y_pos = plot_y + norm_y * plot_h
            lbl.pos = (self.x, y_pos - lbl.height / 2)

            if y_val == 0:
                self.grid_lines.add(Color(0.85, 0.85, 0.85, 1))
                self.grid_lines.add(Line(points=[plot_x, y_pos, plot_x + plot_w, y_pos], width=1.2))
            else:
                self.grid_lines.add(Color(0.94, 0.94, 0.94, 1))
                self.grid_lines.add(Line(points=[plot_x, y_pos, plot_x + plot_w, y_pos], width=1))

        x_range = 1000.0
        for lbl, x_val in self.x_labels:
            norm_x = x_val / x_range
            x_pos = plot_x + norm_x * plot_w

            lbl.pos = (x_pos - lbl.width / 2, self.y)
            self.grid_lines.add(Color(0.94, 0.94, 0.94, 1))
            self.grid_lines.add(Line(points=[x_pos, plot_y, x_pos, plot_y + plot_h], width=1))

        self.grid_lines.add(Color(0.8, 0.8, 0.8, 1))
        self.grid_lines.add(Line(rectangle=(plot_x, plot_y, plot_w, plot_h), width=1))
        self.plot_rect = (plot_x, plot_y, plot_w, plot_h)

    def push_data(self, value):
        """优化后的滤波算法：显著降低增益，抑制溢出噪声"""
        try:
            raw = float(value)

            # 1. 极速导出初始化
            if not hasattr(self, 'init_flag') or not self.init_flag:
                self.lpf = raw
                self.hpf = 0.0
                self.last_raw = raw
                self.init_flag = True

            # 2. 【基线漂移过滤】系数调至 0.92，让波形更稳地停留在0刻度线
            self.hpf = 0.92 * (self.hpf + raw - self.last_raw)
            self.last_raw = raw

            # 3. 【软件低通平滑】增强平滑度，吸收 50Hz 工频噪点
            self.lpf = self.lpf * 0.9 + self.hpf * 0.1

            # 4. 【核心改动：增益调整】
            final_value = self.lpf * 2.5

            # 5. 安全限幅 (防止撞击上下边界)
            final_value = max(-55, min(55, final_value))

            self.ecg_buffer[self.ptr] = final_value
            self.ptr = (self.ptr + 1) % self.data_len

            return round(final_value, 2)
        except:
            return None

    def render(self, dt):
        if not hasattr(self, 'plot_rect'): return
        plot_x, plot_y, plot_w, plot_h = self.plot_rect
        if plot_w <= 0 or plot_h <= 0: return

        points = []
        x_step = plot_w / (self.data_len - 1)

        y_min, y_max = -60.0, 60.0
        y_range = y_max - y_min

        if self.display_mode == 'FLAT':
            norm_y = (0 - y_min) / y_range
            y = plot_y + norm_y * plot_h
            points = [plot_x, y, plot_x + plot_w, y]
        else:
            for i in range(self.data_len):
                idx = (self.ptr + i) % self.data_len
                val = self.ecg_buffer[idx]
                val = max(y_min, min(y_max, val))

                norm_y = (val - y_min) / y_range
                y = plot_y + norm_y * plot_h
                x = plot_x + i * x_step
                points.extend([x, y])

        self.line.points = points


# ==========================================
# 3. 主应用 App (完全保留了你原先的布局占比)
# ==========================================
class ECGApp(App):
    def build(self):
        self.title = "AI辅助心电预警系统 "

        self.current_bpm = 0
        self.current_hrv = 0
        self.current_rhythm = "Normal"
        self.diag_status = 'IDLE'
        self.prep_countdown = 0
        self.valid_data_ticks = 0
        self.rhythm_history = deque(maxlen=10)
        self.last_sms_time = 0
        self.last_valid_data_time = 0

        root = BoxLayout(orientation='vertical', padding=15, spacing=12)

        # ====== 顶部信息 ======
        top_row = BoxLayout(size_hint_y=0.15, spacing=15)
        left_col = BoxLayout(orientation='horizontal', size_hint_x=0.25)

        # 保留了你原版加载 seguiemj.ttf 去显示 "❤️" 文本的逻辑
        self.heart_label = Label(text="❤️", font_size='40sp', halign='center', valign='middle', font_name=EMOJI_FONT)
        self.heart_label.bind(size=self.heart_label.setter('text_size'))

        self.bpm_label = Label(text="--", font_size='36sp', bold=True, color=get_color_from_hex('#333333'),
                               halign='center', valign='middle', font_name=FONT_NAME)
        self.bpm_label.bind(size=self.bpm_label.setter('text_size'))
        left_col.add_widget(self.heart_label)
        left_col.add_widget(self.bpm_label)
        top_row.add_widget(left_col)

        mid_col = BoxLayout(orientation='vertical', size_hint_x=0.25)
        self.hrv_label = Label(text="HRV: -- ms", font_size='22sp', bold=True, color=get_color_from_hex('#555555'),
                               halign='center', valign='middle', font_name=FONT_NAME)
        self.hrv_label.bind(size=self.hrv_label.setter('text_size'))
        mid_col.add_widget(self.hrv_label)
        top_row.add_widget(mid_col)

        right_col = BoxLayout(orientation='vertical', size_hint_x=0.5)
        self.status_label = Label(text="状态: 待机就绪", font_size='20sp', bold=True,
                                  color=get_color_from_hex('#555555'), halign='right', valign='middle',
                                  font_name=FONT_NAME)
        self.status_label.bind(size=self.status_label.setter('text_size'))

        self.btn_diag = Button(text="开始进行诊断", size_hint_y=0.6, font_size='18sp', bold=True,
                               background_color=get_color_from_hex('#0078D7'), color=(1, 1, 1, 1), font_name=FONT_NAME)
        self.btn_diag.bind(on_press=self.start_manual_diagnosis)

        right_col.add_widget(self.status_label)
        right_col.add_widget(self.btn_diag)
        top_row.add_widget(right_col)
        root.add_widget(top_row)

        # ====== 中部波形图 ======
        self.graph = ECGPlotWidget(size_hint_y=0.6)
        root.add_widget(self.graph)

        # ====== 底部日志 ======
        self.advice_box = RichLogBox(size_hint_y=0.25)
        self.advice_box.text = f"{E('💡')} 系统核心引擎启动，连接协议寻址中...\n(注：请确保单片机电极片已可靠粘连皮肤)"
        root.add_widget(self.advice_box)

        Clock.schedule_interval(self.update_ui, 0.5)
        self.heart_anim_event = Clock.schedule_interval(self.animate_heart, 0.8)

        self.hw_thread = HardwareThread(self.on_serial_data, self.update_conn_ui)
        self.hw_thread.start()

        self.csv_manager = CSVDataManager()

        return root

    def update_conn_ui(self, msg):
        Clock.schedule_once(lambda dt: setattr(self.advice_box, 'text', msg), 0)

    def on_serial_data(self, ecg_val, bpm_val, hrv_val, rhythm_str):
        self.last_valid_data_time = time.time()

        clean_ecg = self.graph.push_data(ecg_val)

        if clean_ecg is not None and self.diag_status == 'RUNNING':
            self.csv_manager.save_waveform(clean_ecg)

        if bpm_val > 0:
            self.current_bpm = bpm_val
            self.current_hrv = hrv_val
            if rhythm_str != "Wait":
                self.current_rhythm = rhythm_str

        if self.diag_status != 'IDLE':
            self.graph.display_mode = 'WAVE'

    def animate_heart(self, dt):
        self.heart_label.font_size = 46
        self.heart_label.color = get_color_from_hex('#c0392b')
        Clock.schedule_once(lambda dt: self.reset_heart(), 0.15)

        if self.current_bpm > 30 and self.diag_status != 'IDLE':
            interval = max(300, min(60000 // self.current_bpm, 2000))
            self.heart_anim_event.cancel()
            self.heart_anim_event = Clock.schedule_interval(self.animate_heart, interval / 1000.0)
        else:
            self.heart_anim_event.cancel()
            self.heart_anim_event = Clock.schedule_interval(self.animate_heart, 0.8)

    def reset_heart(self):
        self.heart_label.font_size = 40
        self.heart_label.color = get_color_from_hex('#e74c3c')

    def start_manual_diagnosis(self, instance):
        self.diag_status = 'PREPARING'
        self.prep_countdown = 6
        self.btn_diag.text = "消解杂波中..."
        self.btn_diag.disabled = True
        self.status_label.text = f"基线平复倒数: {self.prep_countdown} 秒..."
        self.status_label.color = get_color_from_hex('#2980b9')
        self.bpm_label.text = "--"
        self.hrv_label.text = "HRV: -- ms"

        self.graph.display_mode = 'WAVE'
        self.last_valid_data_time = time.time()

        self.advice_box.text = (
            "【贴片平复期】侦测探头已唤醒。受电极接触与坐姿影响，前几秒数值易漂移。\n"
            f"   {E('👉')} 请保持深呼吸并贴紧皮肤，静候 6 秒消除物理干扰。"
        )

    def update_ui(self, dt):
        now_time = time.time()
        is_disconnected = (now_time - self.last_valid_data_time) > 1.5

        if self.diag_status == 'IDLE':
            self.bpm_label.text = "--"
            self.hrv_label.text = "HRV: -- ms"
            self.graph.display_mode = 'FLAT'
            return

        if is_disconnected:
            self.bpm_label.text = "--"
            self.hrv_label.text = "HRV: -- ms"
            self.graph.display_mode = 'FLAT'

            if self.diag_status in ['PREPARING', 'RUNNING']:
                self.advice_box.text = (
                    f"{E('⚠️')}【检测中断警告：链路静默】\n"
                    "未侦测到实时硬件波形！请确认：\n"
                    "1. 连接线是否插稳，COM/蓝牙 通讯端是否被占用。\n"
                    "2. 传感器导联金属片是否完全贴紧肌肤导电。"
                )
                self.diag_status = 'IDLE'
                self.btn_diag.disabled = False
                self.btn_diag.text = "重置以打通硬体链路"
                self.status_label.text = "状态: 失去硬件连接响应"
                self.status_label.color = get_color_from_hex('#e74c3c')
            return
        else:
            if self.current_bpm > 0:
                self.bpm_label.text = str(self.current_bpm)
                self.hrv_label.text = f"HRV: {self.current_hrv} ms"

        if self.diag_status == 'PREPARING':
            self.prep_countdown -= 0.5
            if self.prep_countdown > 0:
                self.status_label.text = f"基线平复倒数: {int(self.prep_countdown)} 秒"
            else:
                self.diag_status = 'RUNNING'
                self.valid_data_ticks = 0
                self.rhythm_history.clear()
                self.status_label.text = "状态: 纯净数据抽样中..."
                self.status_label.color = get_color_from_hex('#D35400')
                self.advice_box.text = (
                    "【硬件握手成功】波形稳定入轨，启动内源测录。\n"
                    f"   {E('👉')} 请持续保持平稳状态，等待进度条走完。"
                )
            return

        if self.diag_status == 'DONE': return

        if self.current_bpm > 180:
            self.advice_box.text = f"{E('⚠️')}【高杂波阻滞】捕捉到过激杂音源，数据进度挂起等待排空..."
            self.valid_data_ticks = max(0, self.valid_data_ticks - 1)
            return

        self.valid_data_ticks += 0.5
        if int(self.valid_data_ticks) > len(self.rhythm_history):
            self.rhythm_history.append(self.current_rhythm)

        if self.valid_data_ticks <= 10:
            prog = int((self.valid_data_ticks / 10.0) * 100)
            self.status_label.text = f"智能深部析出... {prog}%"
            return

        afib = self.rhythm_history.count("AFib")
        pvc = self.rhythm_history.count("PVC")

        if afib >= 3:
            self.status_label.text = "诊断结果: 疑似房颤 (AFib)"
            self.status_label.color = get_color_from_hex('#c0392b')
            self.advice_box.text = f"{E('❌')} 【结论快照】捕捉到严重不规律的RR间期序列，强烈疑似心房颤动！"
            self.show_alert_popup("⚠️ 高危心电异常预警",
                                  "系统检测到连续的不规则 RR 间期（疑似房颤），\n请立刻持设备就诊查验！")

        elif pvc >= 3:
            self.status_label.text = "诊断结果: 室性早搏 (PVC)"
            self.status_label.color = get_color_from_hex('#d35400')
            self.advice_box.text = f"{E('⚠️')} 【结论快照】捕捉到部分导联的代偿间歇偏移，疑似室性期前收缩(PVC)。"
            self.show_alert_popup("⚠️ 注意: 节律异常",
                                  "捕捉到提前漏跳的代偿间歇（早搏），\n偶尔发生属正常现象，若频繁出现请注意休息。")

        else:
            self.status_label.text = "诊断结果: 正常心律"
            self.status_label.color = get_color_from_hex('#27ae60')
            self.advice_box.text = f"{E('✅')} 【结论快照】心房心室起搏平稳健康，周期排查完毕，系统解锁进入恒向守护。"

        self.diag_status = 'DONE'
        self.btn_diag.disabled = False
        self.btn_diag.text = "复位并开启全新捕获"

    def trigger_sms_alert(self, msg):
        now = time.time()
        if now - self.last_sms_time > 30:
            self.advice_box.text += f"\n\n{E('🔔')} 【系统分发】异常心动信号已通过后台基站通报管理端。"
            self.last_sms_time = now

    def show_alert_popup(self, title_text, msg_text):
        box = BoxLayout(orientation='vertical', padding=20, spacing=20)
        lbl = Label(text=msg_text, font_name=FONT_NAME, font_size='18sp',
                    color=(1, 1, 1, 1), halign='center', valign='middle')
        lbl.bind(size=lbl.setter('text_size'))

        btn = Button(text="确认并关闭", size_hint_y=0.4, font_name=FONT_NAME,
                     font_size='18sp', bold=True, background_color=get_color_from_hex('#e74c3c'))

        box.add_widget(lbl)
        box.add_widget(btn)

        popup = Popup(title=title_text, content=box, size_hint=(0.7, 0.4),
                      title_font=FONT_NAME, title_color=(1, 0.2, 0.2, 1),
                      auto_dismiss=False)
        btn.bind(on_press=popup.dismiss)
        popup.open()

    def on_stop(self):
        self.hw_thread.stop()


if __name__ == '__main__':
    ECGApp().run()
