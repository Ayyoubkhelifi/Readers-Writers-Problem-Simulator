"""
Advanced Reader-Writer Problem Simulator
With Modern PyQt6 Interface and Multiple Synchronization Strategies
YOUR ORIGINAL DESIGN — ONLY MADE SCROLLABLE
"""

import sys
import threading
import time
import random
from datetime import datetime
from collections import deque
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLabel, QFrame,
                             QTextEdit, QRadioButton, QGroupBox,
                             QGridLayout, QSpinBox, QCheckBox, QScrollArea,
                             QSizePolicy, QGraphicsOpacityEffect)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QThread, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QFont, QColor, QMovie

# --- ANIMATION UTILS ---
def fade_in(widget, duration=400):
    effect = QGraphicsOpacityEffect()
    widget.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity")
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.InOutQuad)
    anim.setDuration(duration)
    anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    return anim

# --- MODERN BUTTON FIX: color palette mapped to better tones
def get_palette():
    return {
        'background': "#18181b",     # bg
        'panel':     "#23243a",     # panel
        'header':    "#8b5cf6",
        'reader':    "#38bdf8",
        'writer':    "#fbbf24",
        'fwarn':     "#f43f5e",
        'stat':      "#334155",
        'row':       "#22223b",
        'blip':      "#05c46b",
        'border':    "#363a55",
        'card':      "#29293d",
        'contrast':  "#e0e7ef",
        'highlight': "#00c3ff",
        'info':      "#4ade80",
        'error':     "#ef4444",
        'wait':      "#ffe066"
    }
palette = get_palette()

class QueueChip(QFrame):
    def __init__(self, text, color):
        super().__init__()
        self.setStyleSheet(f"background: {color}; padding:4px 10px; border-radius:9px; color:#222; font-weight:bold; margin:2px;")
        l = QHBoxLayout(self)
        l.setContentsMargins(0,0,0,0)
        lab = QLabel(text)
        l.addWidget(lab)
        lab.setStyleSheet("font-size:13px;")
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)

class AnimatedStatus(QLabel):
    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(35)
        self.setStyleSheet("border:none;")
    def set_status(self, txt, color):
        self.setText(txt)
        self.setStyleSheet(f"color:#fff; font-weight:bold; background:{color}; border-radius:16px; padding:6px 18px;font-size:14px;margin:5px auto;")
        fade_in(self, 400)


class Semaphore:
    """Thread-safe semaphore implementation"""
    def __init__(self, value=1):
        self._value = value
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
   
    def acquire(self, timeout=None):
        with self._condition:
            start_time = time.time() if timeout else None
            while self._value <= 0:
                if timeout:
                    elapsed = time.time() - start_time
                    if elapsed >= timeout:
                        return False
                    self._condition.wait(timeout - elapsed)
                else:
                    self._condition.wait()
            self._value -= 1
            return True
   
    def release(self):
        with self._condition:
            self._value += 1
            self._condition.notify()
   
    def get_value(self):
        with self._lock:
            return self._value


class SharedResource:
    """Enhanced shared resource with multiple synchronization strategies"""
    def __init__(self, mode='readers', starvation_prevention=True):
        self.data = 0
        self.mode = mode
        self.starvation_prevention = starvation_prevention
        self.readers_count = 0
        self.writers_waiting = 0
        self.readers_waiting = 0
       
        # Synchronization primitives
        self.mutex = Semaphore(1)
        self.write_lock = Semaphore(1)
        self.read_try = Semaphore(1)
        self.order_lock = Semaphore(1)  # For fair mode
       
        # Tracking
        self.total_reads = 0
        self.total_writes = 0
        self.active_writer = None
        self.active_readers = []
        self.waiting_readers_list = []
        self.waiting_writers_list = []
       
        # Performance metrics
        self.read_wait_times = deque(maxlen=100)
        self.write_wait_times = deque(maxlen=100)
        self.max_concurrent_readers = 0
       
        # Starvation prevention
        self.writer_priority_threshold = 5
        self.max_wait_time = 10.0
       
    def get_avg_wait_time(self, process_type):
        times = self.read_wait_times if process_type == 'reader' else self.write_wait_times
        return sum(times) / len(times) if times else 0


class ReaderThread(QThread):
    finished = pyqtSignal(int, float)
    started = pyqtSignal(int)
    waiting = pyqtSignal(int)
   
    def __init__(self, reader_id, resource, signals):
        super().__init__()
        self.reader_id = reader_id
        self.resource = resource
        self.signals = signals
        self.running = True
        self.wait_start = None
        self.daemon = True
   
    def run(self):
        try:
            self.wait_start = time.time()
            self.waiting.emit(self.reader_id)
           
            self.resource.mutex.acquire()
            self.resource.readers_waiting += 1
            self.resource.waiting_readers_list.append(self.reader_id)
            self.resource.mutex.release()
           
            acquired = False
            if self.resource.mode == 'writers':
                while self.running:
                    self.resource.mutex.acquire()
                    writers_waiting = self.resource.writers_waiting
                    self.resource.mutex.release()
                    if writers_waiting == 0:
                        break
                    if self.resource.starvation_prevention:
                        if time.time() - self.wait_start > self.resource.max_wait_time:
                            break
                    time.sleep(0.1)
                    if not self.running:
                        return
                acquired = self._acquire_read_lock()
               
            elif self.resource.mode == 'fair':
                if not self.resource.order_lock.acquire(timeout=15):
                    self._cleanup_waiting()
                    return
                acquired = self._acquire_read_lock()
                self.resource.order_lock.release()
               
            else:
                acquired = self._acquire_read_lock()
           
            if not acquired or not self.running:
                self._cleanup_waiting()
                return
           
            wait_time = time.time() - self.wait_start
            self.resource.read_wait_times.append(wait_time)
           
            self.resource.mutex.acquire()
            self.resource.active_readers.append(self.reader_id)
            current_readers = len(self.resource.active_readers)
            self.resource.max_concurrent_readers = max(
                self.resource.max_concurrent_readers, current_readers
            )
            self.resource.mutex.release()
           
            self.started.emit(self.reader_id)
            data_value = self.resource.data
            self.signals.log_event.emit(
                f"Reader {self.reader_id} reading data: {data_value} (waited {wait_time:.2f}s)", "read"
            )
           
            time.sleep(random.uniform(0.5, 2.0))
           
            if not self.running:
                self._release_read_lock()
                return
           
            self._release_read_lock()
            self.signals.log_event.emit(f"Reader {self.reader_id} finished", "info")
            self.finished.emit(self.reader_id, wait_time)
           
        except Exception as e:
            self.signals.log_event.emit(f"Reader {self.reader_id} error: {e}", "error")
            self._cleanup_waiting()
   
    def _acquire_read_lock(self):
        if not self.resource.read_try.acquire(timeout=15):
            return False
        if not self.resource.mutex.acquire(timeout=5):
            self.resource.read_try.release()
            return False
        self.resource.readers_count += 1
        if self.resource.readers_count == 1:
            if not self.resource.write_lock.acquire(timeout=15):
                self.resource.readers_count -= 1
                self.resource.mutex.release()
                self.resource.read_try.release()
                return False
        self.resource.readers_waiting -= 1
        if self.reader_id in self.resource.waiting_readers_list:
            self.resource.waiting_readers_list.remove(self.reader_id)
        self.resource.mutex.release()
        self.resource.read_try.release()
        return True
   
    def _release_read_lock(self):
        self.resource.mutex.acquire()
        if self.reader_id in self.resource.active_readers:
            self.resource.active_readers.remove(self.reader_id)
        self.resource.readers_count -= 1
        if self.resource.readers_count == 0:
            self.resource.write_lock.release()
        self.resource.total_reads += 1
        self.resource.mutex.release()
   
    def _cleanup_waiting(self):
        self.resource.mutex.acquire()
        self.resource.readers_waiting = max(0, self.resource.readers_waiting - 1)
        if self.reader_id in self.resource.waiting_readers_list:
            self.resource.waiting_readers_list.remove(self.reader_id)
        self.resource.mutex.release()
   
    def stop(self):
        self.running = False


class WriterThread(QThread):
    finished = pyqtSignal(int, int, float)
    started = pyqtSignal(int, int)
    waiting = pyqtSignal(int)
   
    def __init__(self, writer_id, resource, signals):
        super().__init__()
        self.writer_id = writer_id
        self.resource = resource
        self.signals = signals
        self.running = True
        self.wait_start = None
        self.daemon = True
   
    def run(self):
        try:
            self.wait_start = time.time()
            self.waiting.emit(self.writer_id)

            self.resource.mutex.acquire()
            self.resource.writers_waiting += 1
            self.resource.waiting_writers_list.append(self.writer_id)
            self.resource.mutex.release()

            acquired = False
            if self.resource.mode == 'readers':
                # STRICT readers-priority with atomic check before lock acquisition
                while self.running:
                    self.resource.mutex.acquire()
                    readers_active = self.resource.readers_count
                    readers_waiting = self.resource.readers_waiting
                    self.resource.mutex.release()
                    if readers_active == 0 and readers_waiting == 0:
                        # Recheck: atomic wait for lock *AND* for zero waiting readers
                        if self.resource.read_try.acquire(timeout=1):
                            if self.resource.write_lock.acquire(timeout=1):
                                self.resource.mutex.acquire()
                                # CHECK AGAIN: No new reader joined while waiting for locks!
                                if self.resource.readers_waiting == 0:
                                    self.resource.writers_waiting -= 1
                                    if self.writer_id in self.resource.waiting_writers_list:
                                        self.resource.waiting_writers_list.remove(self.writer_id)
                                    self.resource.mutex.release()
                                    acquired = True
                                    break
                                # If a new reader joined, let go and loop again:
                                self.resource.mutex.release()
                                self.resource.write_lock.release()
                            self.resource.read_try.release()
                    if self.resource.starvation_prevention:
                        if time.time() - self.wait_start > self.resource.max_wait_time:
                            break
                    time.sleep(0.04)
                    if not self.running:
                        return
            elif self.resource.mode == 'fair':
                if not self.resource.order_lock.acquire(timeout=20):
                    self._cleanup_waiting()
                    return
                acquired = self._acquire_write_lock()
                self.resource.order_lock.release()
            else:
                acquired = self._acquire_write_lock()
            if not acquired or not self.running:
                self._cleanup_waiting()
                return
           
            wait_time = time.time() - self.wait_start
            self.resource.write_wait_times.append(wait_time)
           
            self.resource.active_writer = self.writer_id
            old_data = self.resource.data
            self.resource.data += 1
            new_data = self.resource.data
           
            self.started.emit(self.writer_id, new_data)
            self.signals.log_event.emit(
                f"Writer {self.writer_id} writing: {old_data} → {new_data} (waited {wait_time:.2f}s)", "write"
            )
           
            time.sleep(random.uniform(1.0, 3.0))
           
            if not self.running:
                self._release_write_lock()
                return
           
            self._release_write_lock()
            self.signals.log_event.emit(f"Writer {self.writer_id} finished", "info")
            self.finished.emit(self.writer_id, new_data, wait_time)
           
        except Exception as e:
            self.signals.log_event.emit(f"Writer {self.writer_id} error: {e}", "error")
            self._cleanup_waiting()
   
    def _acquire_write_lock(self):
        if not self.resource.read_try.acquire(timeout=20):
            return False
        if not self.resource.write_lock.acquire(timeout=20):
            self.resource.read_try.release()
            return False
        self.resource.mutex.acquire()
        self.resource.writers_waiting -= 1
        if self.writer_id in self.resource.waiting_writers_list:
            self.resource.waiting_writers_list.remove(self.writer_id)
        self.resource.mutex.release()
        return True
   
    def _release_write_lock(self):
        self.resource.active_writer = None
        self.resource.total_writes += 1
        self.resource.write_lock.release()
        self.resource.read_try.release()
   
    def _cleanup_waiting(self):
        self.resource.mutex.acquire()
        self.resource.writers_waiting = max(0, self.resource.writers_waiting - 1)
        if self.writer_id in self.resource.waiting_writers_list:
            self.resource.waiting_writers_list.remove(self.writer_id)
        self.resource.mutex.release()
   
    def stop(self):
        self.running = False


class Signals(QObject):
    log_event = pyqtSignal(str, str)
    update_stats = pyqtSignal()


class ModernButton(QPushButton):
    def __init__(self, text, color="blue"):
        super().__init__(text)
        colors = {
            "blue": "#3B82F6", "green": "#10B981", "red": "#EF4444",
            "gray": "#6B7280", "orange": "#F97316", "purple": "#8B5CF6"
        }
        c = colors.get(color, colors["blue"])
        self.setMinimumHeight(40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(f"""
            QPushButton {{
                background: {c};
                color: white;
                border: none;
                border-radius: 10px;
                padding: 10px 20px;
                font-weight: bold;
                font-size: 13px;
            }}
            QPushButton:hover {{ background: {c}CC; }}
            QPushButton:pressed {{ background: {c}AA; }}
        """)


class StatsCard(QFrame):
    def __init__(self, title, value="0", color="#3B82F6"):
        super().__init__()
        self.setStyleSheet(f"""
            QFrame {{
                background: rgba(255,255,255,0.1);
                border: 2px solid rgba(255,255,255,0.2);
                border-radius: 12px;
                padding: 15px;
            }}
        """)
        layout = QVBoxLayout()
        self.value_label = QLabel(value)
        self.value_label.setStyleSheet(f"font-size: 32px; font-weight: bold; color: {color};")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 12px; color: rgba(255,255,255,0.7);")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.value_label)
        layout.addWidget(title_label)
        self.setLayout(layout)
   
    def update_value(self, value):
        self.value_label.setText(str(value))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Advanced Reader-Writer Problem Simulator")
        self.setGeometry(100, 100, 1500, 950)

        self.resource = SharedResource()
        self.signals = Signals()
        self.reader_threads = []
        self.writer_threads = []
        self.is_running = False
        self.next_id = 1

        self.setup_ui()

        self.signals.log_event.connect(self.add_log)

        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.auto_update)
        self.update_timer.start(300)

        self.create_timer = QTimer()
        self.create_timer.timeout.connect(self.auto_create_processes)

    def setup_ui(self):
        # SCROLL AREA — ONLY CHANGE
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        central_widget = QWidget()
        scroll.setWidget(central_widget)

        self.setCentralWidget(scroll)

        # YOUR ORIGINAL STYLES AND LAYOUT
        # Background: deep blue #000319 to purple #a78bfa gradient (applied to both main window and content)
        gradient_css = "background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #000319, stop:1 #a78bfa);"
        self.setStyleSheet(f"QMainWindow {{{gradient_css}}} QLabel {{ color: white; }} QTextEdit {{ background: rgba(255,255,255,0.1); border: 2px solid rgba(255,255,255,0.2); border-radius: 10px; color: white; padding: 10px; font-size: 12px; }} QRadioButton, QCheckBox {{ color: white; font-size: 12px; }} QGroupBox {{ color: white; font-weight: bold; border: 2px solid rgba(255,255,255,0.2); border-radius: 10px; margin-top: 10px; padding-top: 10px; background: rgba(255,255,255,0.05); }} QSpinBox {{ background: rgba(255,255,255,0.1); border: 2px solid rgba(255,255,255,0.2); border-radius: 6px; color: white; padding: 5px; font-size: 12px; }}")
        central_widget.setStyleSheet(gradient_css)
        central_widget.setAutoFillBackground(True)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(25, 25, 25, 25)

        # Header
        header = QLabel("Advanced Reader-Writer Problem Simulator")
        header.setStyleSheet("font-size: 28px; font-weight: bold; padding: 15px;")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(header)

        # Controls
        controls_layout = QHBoxLayout()
        self.start_btn = ModernButton("Start", palette['info'])
        self.start_btn.clicked.connect(self.toggle_simulation)
        self.reset_btn = ModernButton("Reset", "gray")
        self.reset_btn.clicked.connect(self.reset_simulation)
        self.add_reader_btn = ModernButton("+ Reader", palette['reader'])
        self.add_reader_btn.clicked.connect(self.add_reader)
        self.add_writer_btn = ModernButton("+ Writer", palette['writer'])
        self.add_writer_btn.clicked.connect(self.add_writer)

        controls_layout.addWidget(self.start_btn)
        controls_layout.addWidget(self.reset_btn)
        controls_layout.addStretch()
        controls_layout.addWidget(self.add_reader_btn)
        controls_layout.addWidget(self.add_writer_btn)
        main_layout.addLayout(controls_layout)

        # Mode and settings
        settings_layout = QHBoxLayout()
        mode_group = QGroupBox("Priority Mode")
        mode_layout = QHBoxLayout()
        self.mode_readers = QRadioButton("Readers Priority")
        self.mode_writers = QRadioButton("Writers Priority")
        self.mode_fair = QRadioButton("Fair (Anti-Starvation)")
        self.mode_fair.setChecked(True)

        self.mode_readers.toggled.connect(lambda: self.change_mode('readers'))
        self.mode_writers.toggled.connect(lambda: self.change_mode('writers'))
        self.mode_fair.toggled.connect(lambda: self.change_mode('fair'))

        mode_layout.addWidget(self.mode_readers)
        mode_layout.addWidget(self.mode_writers)
        mode_layout.addWidget(self.mode_fair)
        mode_group.setLayout(mode_layout)
        settings_layout.addWidget(mode_group)

        options_group = QGroupBox("Options")
        options_layout = QHBoxLayout()
        self.starvation_check = QCheckBox("Starvation Prevention")
        self.starvation_check.setChecked(True)
        self.starvation_check.toggled.connect(self.toggle_starvation_prevention)
        options_layout.addWidget(self.starvation_check)
        self.auto_add_check = QCheckBox("Auto Add Processes")
        self.auto_add_check.setChecked(True)
        self.auto_add_check.toggled.connect(self.toggle_auto_add)
        options_layout.insertWidget(0, self.auto_add_check)
        options_layout.addWidget(QLabel("Auto-create interval (ms):"))
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(500, 10000)
        self.interval_spin.setValue(2000)
        self.interval_spin.setSingleStep(500)
        options_layout.addWidget(self.interval_spin)
        options_group.setLayout(options_layout)
        settings_layout.addWidget(options_group)

        main_layout.addLayout(settings_layout)

        # Main content
        content_layout = QHBoxLayout()

        # Left panel
        left_layout = QVBoxLayout()

        resource_frame = QFrame()
        resource_frame.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #7C3AED, stop:1 #3B82F6);
                border-radius: 12px;
                padding: 25px;
            }
        """)
        resource_layout = QVBoxLayout()
        resource_title = QLabel("Shared Resource")
        resource_title.setStyleSheet("font-size: 18px; font-weight: bold;")
        self.data_label = QLabel("0")
        self.data_label.setStyleSheet("font-size: 64px; font-weight: bold;")
        self.data_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label = AnimatedStatus()
        resource_layout.addWidget(resource_title)
        resource_layout.addWidget(self.data_label)
        resource_layout.addWidget(self.status_label)
        resource_frame.setLayout(resource_layout)
        left_layout.addWidget(resource_frame)

        stats_layout = QGridLayout()
        self.active_readers_card = StatsCard("Active Readers", "0", palette['reader'])
        self.active_writer_card = StatsCard("Active Writer", "0", palette['writer'])
        self.total_reads_card = StatsCard("Total Reads", "0", palette['blip'])
        self.total_writes_card = StatsCard("Total Writes", "0", palette['error'])
        self.max_readers_card = StatsCard("Max Concurrent", "0", palette['header'])
        self.avg_wait_card = StatsCard("Avg Wait (s)", "0.0", palette['wait'])

        stats_layout.addWidget(self.active_readers_card, 0, 0)
        stats_layout.addWidget(self.active_writer_card, 0, 1)
        stats_layout.addWidget(self.total_reads_card, 1, 0)
        stats_layout.addWidget(self.total_writes_card, 1, 1)
        stats_layout.addWidget(self.max_readers_card, 2, 0)
        stats_layout.addWidget(self.avg_wait_card, 2, 1)

        left_layout.addLayout(stats_layout)

        # Right panel
        right_layout = QVBoxLayout()

        queue_frame = QFrame()
        queue_frame.setStyleSheet("""
            QFrame {
                background: rgba(255,255,255,0.1);
                border: 2px solid rgba(255,255,255,0.2);
                border-radius: 12px;
                padding: 15px;
            }
        """)
        queue_layout = QVBoxLayout()
        queue_title = QLabel("Waiting Queues")
        queue_title.setStyleSheet("font-size: 16px; font-weight: bold;")
        queue_layout.addWidget(queue_title)
        queue_layout.addWidget(QLabel("Readers waiting:"))
        self.readers_queue = QLabel("None")
        self.readers_queue.setStyleSheet("color: #93C5FD; margin-left: 10px;")
        self.readers_queue.setWordWrap(True)
        queue_layout.addWidget(self.readers_queue)
        queue_layout.addWidget(QLabel("Writers waiting:"))
        self.writers_queue = QLabel("None")
        self.writers_queue.setStyleSheet("color: #FED7AA; margin-left: 10px;")
        self.writers_queue.setWordWrap(True)
        queue_layout.addWidget(self.writers_queue)
        queue_frame.setLayout(queue_layout)
        right_layout.addWidget(queue_frame)

        log_frame = QFrame()
        log_frame.setStyleSheet("""
            QFrame {
                background: rgba(255,255,255,0.1);
                border: 2px solid rgba(255,255,255,0.2);
                border-radius: 12px;
                padding: 15px;
            }
        """)
        log_layout = QVBoxLayout()
        log_title = QLabel("Event Log")
        log_title.setStyleSheet("font-size: 16px; font-weight: bold;")
        log_layout.addWidget(log_title)
        self.log_display = QTextEdit()
        self.log_display.setReadOnly(True)
        log_layout.addWidget(self.log_display)
        log_frame.setLayout(log_layout)
        right_layout.addWidget(log_frame)

        content_layout.addLayout(left_layout, 1)
        content_layout.addLayout(right_layout, 1)
        main_layout.addLayout(content_layout)

    def set_add_buttons(self, enabled):
        self.add_reader_btn.setEnabled(enabled)
        self.add_writer_btn.setEnabled(enabled)

    def toggle_simulation(self):
        self.is_running = not self.is_running
        self.start_btn.setText("Pause" if self.is_running else "Start")
        if self.is_running and self.auto_add_check.isChecked():
            self.create_timer.start(self.interval_spin.value())
        else:
            self.create_timer.stop()
        self.set_add_buttons(self.is_running)

    def toggle_auto_add(self, checked):
        if checked and self.is_running:
            self.create_timer.start(self.interval_spin.value())
        else:
            self.create_timer.stop()

    def change_mode(self, mode):
        self.resource.mode = mode
        self.add_log(f"Mode changed: {mode}", "info")

    def toggle_starvation_prevention(self, checked):
        self.resource.starvation_prevention = checked
        status = "enabled" if checked else "disabled"
        self.add_log(f"Starvation prevention {status}", "info")

    def add_reader(self):
        if not self.is_running:
            return
        curr_id = self.next_id
        self.next_id += 1
        # Prevent adding duplicate-running threads
        if any(r.reader_id == curr_id and r.isRunning() for r in self.reader_threads):
            return
        reader = ReaderThread(curr_id, self.resource, self.signals)
        reader.finished.connect(self.on_reader_finished)
        self.reader_threads.append(reader)
        reader.start()

    def add_writer(self):
        if not self.is_running:
            return
        curr_id = self.next_id
        self.next_id += 1
        if any(w.writer_id == curr_id and w.isRunning() for w in self.writer_threads):
            return
        writer = WriterThread(curr_id, self.resource, self.signals)
        writer.finished.connect(self.on_writer_finished)
        self.writer_threads.append(writer)
        writer.start()

    def auto_create_processes(self):
        if not self.is_running:
            return
        if random.random() < 0.7:
            self.add_reader()
        if random.random() < 0.3:
            self.add_writer()

    def on_reader_finished(self, reader_id, wait_time):
        self.reader_threads = [r for r in self.reader_threads if r.reader_id != reader_id or not r.isFinished()]

    def on_writer_finished(self, writer_id, new_data, wait_time):
        self.writer_threads = [w for w in self.writer_threads if w.writer_id != writer_id or not w.isFinished()]

    def auto_update(self):
        self.data_label.setText(str(self.resource.data))
        self.active_readers_card.update_value(len(self.resource.active_readers))
        self.active_writer_card.update_value(1 if self.resource.active_writer else 0)
        self.total_reads_card.update_value(self.resource.total_reads)
        self.total_writes_card.update_value(self.resource.total_writes)
        self.max_readers_card.update_value(self.resource.max_concurrent_readers)

        avg_wait = (self.resource.get_avg_wait_time('reader') + self.resource.get_avg_wait_time('writer')) / 2
        self.avg_wait_card.update_value(f"{avg_wait:.2f}")

        self.readers_queue.setText(", ".join([f"R{r}" for r in self.resource.waiting_readers_list]) or "None")
        self.writers_queue.setText(", ".join([f"W{w}" for w in self.resource.waiting_writers_list]) or "None")
        self.set_add_buttons(self.is_running)
        # Visually show auto mode state (optional: could gray out interval spinbox if not auto mode)
        self.interval_spin.setEnabled(self.auto_add_check.isChecked())

    def add_log(self, message, log_type):
        timestamp = datetime.now().strftime("%H:%M:%S")
        colors = {"read": "#60A5FA", "write": "#FB923C", "info": "#A3A3A3", "error": "#EF4444"}
        color = colors.get(log_type, "#FFFFFF")
        self.log_display.append(f'<span style="color: {color};">[{timestamp}] {message}</span>')
        if self.log_display.document().blockCount() > 200:
            text = self.log_display.toPlainText()
            lines = text.split('\n')
            self.log_display.setPlainText('\n'.join(lines[-200:]))
        self.log_display.verticalScrollBar().setValue(self.log_display.verticalScrollBar().maximum())

    def reset_simulation(self):
        self.is_running = False
        self.start_btn.setText("Start")
        self.create_timer.stop()
        self.set_add_buttons(False)

        for reader in self.reader_threads:
            reader.stop()
        for writer in self.writer_threads:
            writer.stop()

        for reader in self.reader_threads:
            reader.wait()
        for writer in self.writer_threads:
            writer.wait()

        self.reader_threads = []
        self.writer_threads = []

        self.resource = SharedResource(mode=self.resource.mode, starvation_prevention=self.resource.starvation_prevention)

        self.next_id = 1
        self.log_display.clear()
        self.data_label.setText("0")
        self.readers_queue.setText("None")
        self.writers_queue.setText("None")
        self.active_readers_card.update_value("0")
        self.active_writer_card.update_value("0")
        self.total_reads_card.update_value("0")
        self.total_writes_card.update_value("0")
        self.max_readers_card.update_value("0")
        self.avg_wait_card.update_value("0.0")

        self.add_log("Simulation reset", "info")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())