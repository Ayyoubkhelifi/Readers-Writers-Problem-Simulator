"""
Advanced Reader-Writer Problem Simulator
With Modern PyQt6 Interface and Strict Synchronization Strategies
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
                             QGridLayout, QSpinBox, QCheckBox,
                             QSizePolicy, QGraphicsOpacityEffect, QScrollArea,
                             QGraphicsDropShadowEffect)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QThread, QPropertyAnimation, QEasingCurve, QPoint
from PyQt6.QtGui import QColor, QPalette, QBrush, QLinearGradient

# --- LOGIC IMPLEMENTATION ---

class ReadWriteLock:
    """
    Base class for Reader-Writer locks.
    Subclasses implement specific priority strategies.
    Using threading.Condition/Lock/Semaphore for strict OS-like behavior.
    """
    def __init__(self):
        self.mutex = threading.Lock()
        self.active_readers = 0
        self.active_writers = 0
        self.waiting_readers = 0
        self.waiting_writers = 0
        self.log_callback = None
    
    def set_log_callback(self, cb):
        self.log_callback = cb

    def log(self, msg):
        if self.log_callback:
            self.log_callback(msg)

    def start_read(self, thread_id):
        raise NotImplementedError

    def end_read(self, thread_id):
        raise NotImplementedError

    def start_write(self, thread_id):
        raise NotImplementedError

    def end_write(self, thread_id):
        raise NotImplementedError

class ReadersPriorityLock(ReadWriteLock):
    """
    Classic Readers Priority:
    - Readers enter if no writer is strictly ACTIVE.
    - Readers ignore waiting writers (causing Writer Starvation).
    """
    def __init__(self):
        super().__init__()
        self.condition = threading.Condition(self.mutex)

    def start_read(self, thread_id):
        with self.condition:
            # Wait only if a writer is currently WRITING
            while self.active_writers > 0:
                self.condition.wait()
            self.active_readers += 1

    def end_read(self, thread_id):
        with self.condition:
            self.active_readers -= 1
            if self.active_readers == 0:
                self.condition.notify_all() # Wake up potential writers

    def start_write(self, thread_id):
        with self.condition:
            self.waiting_writers += 1
            # Wait until safe
            while self.active_readers > 0 or self.active_writers > 0:
                self.condition.wait()
            self.waiting_writers -= 1
            self.active_writers += 1

    def end_write(self, thread_id):
        with self.condition:
            self.active_writers -= 1
            self.condition.notify_all() # Wake up everyone (readers will jump in first typically)

class WritersPriorityLock(ReadWriteLock):
    """
    Strict Writers Priority:
    - Readers cannot enter if a writer is WAITING or ACTIVE.
    """
    def __init__(self):
        super().__init__()
        self.condition = threading.Condition(self.mutex)

    def start_read(self, thread_id):
        with self.condition:
            # Block if writer is active OR waiting
            while self.active_writers > 0 or self.waiting_writers > 0:
                self.condition.wait()
            self.active_readers += 1

    def end_read(self, thread_id):
        with self.condition:
            self.active_readers -= 1
            if self.active_readers == 0:
                self.condition.notify_all()

    def start_write(self, thread_id):
        with self.condition:
            self.waiting_writers += 1
            while self.active_readers > 0 or self.active_writers > 0:
                self.condition.wait()
            self.waiting_writers -= 1
            self.active_writers += 1

    def end_write(self, thread_id):
        with self.condition:
            self.active_writers -= 1
            self.condition.notify_all()

class TrueFairRWLock(ReadWriteLock):
    """
    True First-Come-First-Served (FIFO) Strategy:
    - Uses a queue to enforce strict ordering.
    - No thread jumps the line.
    """
    def __init__(self):
        super().__init__()
        # Queue stores tuples: (id, type='R'|'W', condition_var)
        self.queue = deque()
        self.active_writer_id = None
        self.lock = threading.Lock()

    def start_read(self, thread_id):
        me = threading.Condition(self.lock)
        with self.lock:
            self.queue.append((thread_id, 'R', me))
            # Wait loop
            while True:
                # Can I enter?
                # 1. No active writer
                # 2. I am at the front OR (I am a reader AND previous in queue are readers who are also entering)
                
                # Logic: Check if I am "admissible"
                if self.active_writers > 0:
                    me.wait()
                    continue
                
                # Check if I am capable of running given queue state
                # Am I the head?
                if self.queue[0][0] == thread_id:
                    # Yes, I am head.
                    self.active_readers += 1
                    self.queue.popleft() # Leave queue, become active
                    
                    # Optimization: If next is also Reader, wake them up too (cascading read)
                    if self.queue and self.queue[0][1] == 'R':
                        self.queue[0][2].notify()
                    break
                
                # If I am NOT head, wait.
                me.wait()

    def end_read(self, thread_id):
        with self.lock:
            self.active_readers -= 1
            if self.active_readers == 0:
                # If queue not empty, wake head (likely a Writer waiting)
                if self.queue:
                    self.queue[0][2].notify()

    def start_write(self, thread_id):
        me = threading.Condition(self.lock)
        with self.lock:
            self.queue.append((thread_id, 'W', me))
            while True:
                # Writer needs:
                # 1. No active readers
                # 2. No active writers
                # 3. Must be at head of queue
                if self.active_readers == 0 and self.active_writers == 0 and self.queue[0][0] == thread_id:
                    self.active_writers = 1
                    self.queue.popleft()
                    break
                me.wait()

    def end_write(self, thread_id):
        with self.lock:
            self.active_writers = 0
            # Wake next
            if self.queue:
                self.queue[0][2].notify()

class AdaptiveRWLock(ReadWriteLock):
    """
    Adaptive (Aging) Strategy:
    - Defaults to Reader Priority (High Concurrency).
    - If a Writer waits > 1.0s (Starvation Risk), switches to Writer Priority.
    """
    def __init__(self):
        super().__init__()
        self.condition = threading.Condition(self.mutex)
        self.writer_arrival_times = {} # tid -> time
        self.STARVATION_THRESHOLD = 2.0 # Seconds before panic mode

    def should_panic(self):
        # Check if any waiting writer is starving
        now = time.time()
        for t in self.writer_arrival_times.values():
            if now - t > self.STARVATION_THRESHOLD:
                return True
        return False

    def start_read(self, thread_id):
        with self.condition:
            while True:
                # Standard check: Block if writing
                if self.active_writers > 0:
                    self.condition.wait()
                    continue
                
                # ADAPTIVE CHECK:
                # If a writer is starving (PANIC MODE), block new readers
                if self.should_panic() and self.waiting_writers > 0:
                     self.condition.wait()
                     continue
                     
                break
                
            self.active_readers += 1

    def end_read(self, thread_id):
        with self.condition:
            self.active_readers -= 1
            if self.active_readers == 0:
                self.condition.notify_all()

    def start_write(self, thread_id):
        with self.condition:
            self.writer_arrival_times[thread_id] = time.time()
            self.waiting_writers += 1
            
            while self.active_readers > 0 or self.active_writers > 0:
                # If I am starving, the readers will eventually stop entering
                self.condition.wait()
                
            self.waiting_writers -= 1
            del self.writer_arrival_times[thread_id]
            self.active_writers += 1

    def end_write(self, thread_id):
        with self.condition:
            self.active_writers -= 1
            self.condition.notify_all()


# --- THREADS ---

class WorkerSignal(QObject):
    log = pyqtSignal(str, str)
    state_changed = pyqtSignal()
    finished_ok = pyqtSignal(int, float)


class GenericWorker(QThread):
    def __init__(self, t_id, lock_manager, shared_data, sleep_range, is_writer=False):
        super().__init__()
        self.t_id = t_id
        self.lock_manager = lock_manager
        self.shared_data = shared_data
        self.sleep_range = sleep_range
        self.is_writer = is_writer
        self.signals = WorkerSignal()
        self.run_flag = True
        self.current_state = "init"  # init, waiting, active, finished

    def run(self):
        # Notify waiting
        self.current_state = "waiting"
        self.signals.log.emit(f"{'Writer' if self.is_writer else 'Reader'} {self.t_id} requesting lock...", "wait")
        
        start_wait = time.time()
        
        if self.is_writer:
            self.lock_manager.start_write(self.t_id)
        else:
            self.lock_manager.start_read(self.t_id)
            
        if not self.run_flag: # Check if we should abort after acquiring
            if self.is_writer: self.lock_manager.end_write(self.t_id)
            else: self.lock_manager.end_read(self.t_id)
            return

        wait_time = time.time() - start_wait
        self.current_state = "active"
        self.signals.log.emit(f"{'Writer' if self.is_writer else 'Reader'} {self.t_id} acquired lock (waited {wait_time:.3f}s)", "active")
        
        # CRITICAL SECTION
        try:
            # Simulate work in chunks to check run_flag
            sleep_tgt = random.uniform(*self.sleep_range)
            steps = 10
            for _ in range(steps):
                if not self.run_flag: break
                time.sleep(sleep_tgt/steps)
                
            if self.run_flag:
                val = self.shared_data['val']
                if self.is_writer:
                    self.shared_data['val'] += 1
                    self.signals.log.emit(f"Writer {self.t_id} updated data: {val} -> {self.shared_data['val']}", "write")
                else:
                    self.signals.log.emit(f"Reader {self.t_id} read data: {val}", "read")
        finally:
            self.current_state = "finished"
            if self.is_writer:
                self.lock_manager.end_write(self.t_id)
            else:
                self.lock_manager.end_read(self.t_id)
            
            self.signals.log.emit(f"{'Writer' if self.is_writer else 'Reader'} {self.t_id} released lock", "free")
            self.signals.finished_ok.emit(self.t_id, wait_time)

    def stop(self):
        self.run_flag = False
        if not self.wait(500):  # Wait up to 500ms for graceful exit
            self.terminate()    # Force kill if blocked in synchronization primitive
            self.wait()         # Ensure cleanup


COLORS = {
    'bg_start': "#0f172a",
    'bg_end': "#1e293b",
    'card_bg': "rgba(30, 41, 59, 0.7)",
    'accent': "#38bdf8",
    'purple': "#8b5cf6",
    'red': "#f43f5e",
    'green': "#10b981",
    'text': "#f8fafc",
    'text_dim': "#94a3b8"
}

class GlassCard(QFrame):
    def __init__(self, layout_func=None):
        super().__init__()
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS['card_bg']};
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 16px;
            }}
        """)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0,0,0,80))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)
        
        if layout_func:
            self.setLayout(layout_func())

class Badge(QLabel):
    def __init__(self, text, color):
        super().__init__(text)
        self.setStyleSheet(f"""
            background-color: {color}20;
            color: {color};
            border: 1px solid {color}60;
            border-radius: 6px;
            padding: 4px 8px;
            font-weight: bold;
            font-size: 11px;
        """)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

class ProcessChip(QLabel):
    def __init__(self, pid, is_writer=False):
        super().__init__(f"{'W' if is_writer else 'R'}{pid}")
        c = COLORS['red'] if is_writer else COLORS['accent']
        self.setStyleSheet(f"""
            background-color: {c};
            color: white;
            border-radius: 12px;
            padding: 5px 10px;
            font-weight: bold;
            min-width: 30px;
            text-align: center;
        """)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(40, 40)
        
        # Animation
        self.anim = QPropertyAnimation(self, b"pos")
        self.anim.setDuration(300)
        self.anim.setEasingCurve(QEasingCurve.Type.OutBack)

class ResourceVisualizer(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(200)
        self.active_chips = {} # pid -> widget
        self.layout_grid = QGridLayout(self)
        self.layout_grid.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Central Data Component
        self.center_box = QLabel("DATA: 0")
        self.center_box.setFixedSize(120, 120)
        self.center_box.setStyleSheet(f"""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {COLORS['purple']}, stop:1 {COLORS['accent']});
            color: white;
            font-weight: bold;
            font-size: 18px;
            border-radius: 60px;
            border: 4px solid rgba(255,255,255,0.3);
        """)
        self.center_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Add simpler container layout
        container = QWidget()
        l = QVBoxLayout(container)
        l.addWidget(self.center_box, 0, Qt.AlignmentFlag.AlignCenter)
        self.layout_grid.addWidget(container, 1, 1)

        # Orbit positions
        self.slots = [(0,1), (1,0), (1,2), (2,1), (0,0), (0,2), (2,0), (2,2)]
        
    def update_state(self, readers, writer, value):
        self.center_box.setText(f"DATA\n{value}")
        
        # Simple rebuild for robustness (could be optimized)
        for c in self.active_chips.values():
            c.deleteLater()
        self.active_chips = {}
        
        current_slot = 0
        
        # Add writer
        if writer:
            w = ProcessChip(writer, True)
            self.layout_grid.addWidget(w, 1, 1, Qt.AlignmentFlag.AlignCenter) 
            # Overlay on top of data circle for writer (exclusive)
            self.active_chips[f"W{writer}"] = w
            
        # Add readers
        else:
            for r in readers:
                if current_slot >= len(self.slots): break
                rc = ProcessChip(r, False)
                row, col = self.slots[current_slot]
                self.layout_grid.addWidget(rc, row, col)
                self.active_chips[f"R{r}"] = rc
                current_slot += 1

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Simulateur Avancé Lecteurs-Rédacteurs")
        self.resize(1200, 850)
        self.shared_data = {'val': 0}
        self.threads = []
        self.next_id = 1
        self.mode = 'fair'
        self.setup_ui()
        self.reset_logic()
        
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_stats)
        self.timer.start(100)

    def closeEvent(self, event):
        self.timer.stop()
        # Force terminate all threads
        for t in self.threads:
            t.run_flag = False
            if t.isRunning():
                t.terminate()
        # Wait for all to finish
        for t in self.threads:
            t.wait()
        self.threads = []
        event.accept()
        
    def reset_logic(self):
        # Stop existing threads gracefully
        for t in self.threads:
            t.stop()
        self.threads = []
        self.shared_data = {'val': 0}
        
        # Metrics Storage
        self.metrics = {
            'R_wait': [],
            'W_wait': [],
            'start_time': time.time(),
            'ops_count': 0
        }
        
        if self.mode == 'readers':
            self.lock = ReadersPriorityLock()
        elif self.mode == 'writers':
            self.lock = WritersPriorityLock()
        elif self.mode == 'adaptive':
            self.lock = AdaptiveRWLock()
        else:
            self.lock = TrueFairRWLock()
            
        self.log_widget.clear()
        self.log_system("System Reset. Mode: " + self.mode.upper())
        self.next_id = 1

    def setup_ui(self):
        main_wid = QWidget()
        self.setCentralWidget(main_wid)
        main_wid.setStyleSheet(f"background-color: {COLORS['bg_start']}; font-family: 'Segoe UI', sans-serif;")
        
        layout = QHBoxLayout(main_wid)
        layout.setSpacing(20)
        layout.setContentsMargins(20,20,20,20)
        
        # --- LEFT PANEL: Controls & Stats ---
        left_panel = QVBoxLayout()
        
        # 1. Header
        title = QLabel("Problème Lecteurs-Rédacteurs")
        title.setStyleSheet(f"font-size: 24px; font-weight: bold; color: {COLORS['text']};")
        left_panel.addWidget(title)
        
        # 2. Strategies
        strat_card = GlassCard(lambda: QVBoxLayout())
        sl = strat_card.layout()
        sl.addWidget(QLabel("Stratégie de Synchronisation", styleSheet="font-weight:bold; color:white;"))
        
        self.rb_fair = QRadioButton("Équitable (True FIFO)")
        self.rb_read = QRadioButton("Priorité Lecteurs")
        self.rb_write = QRadioButton("Priorité Rédacteurs")
        self.rb_adaptive = QRadioButton("Adaptatif (Anti-Famine)")
        
        self.rb_fair.setChecked(True)
        self.rb_fair.toggled.connect(lambda: self.set_mode('fair'))
        self.rb_read.toggled.connect(lambda: self.set_mode('readers'))
        self.rb_write.toggled.connect(lambda: self.set_mode('writers'))
        self.rb_adaptive.toggled.connect(lambda: self.set_mode('adaptive'))
        
        for rb in [self.rb_fair, self.rb_read, self.rb_write, self.rb_adaptive]:
            rb.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 14px; padding: 5px;")
            sl.addWidget(rb)
            
        left_panel.addWidget(strat_card)
        
        # 3. Actions
        act_card = GlassCard(lambda: QVBoxLayout())
        al = act_card.layout()
        
        btn_style = f"""
            QPushButton {{
                background-color: {COLORS['accent']};
                color: #0f172a;
                border: none;
                border-radius: 8px;
                padding: 12px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background-color: white; }}
        """
        
        self.btn_add_reader = QPushButton("+ Ajouter Lecteur")
        self.btn_add_reader.setStyleSheet(btn_style)
        self.btn_add_reader.clicked.connect(self.add_reader)
        
        self.btn_add_writer = QPushButton("+ Ajouter Rédacteur")
        self.btn_add_writer.setStyleSheet(btn_style.replace(COLORS['accent'], COLORS['red']))
        self.btn_add_writer.clicked.connect(self.add_writer)
        
        self.btn_reset = QPushButton("Réinitialiser")
        self.btn_reset.setStyleSheet(f"background:transparent; border:1px solid {COLORS['text_dim']}; color:{COLORS['text_dim']}; padding: 10px; border-radius: 8px;")
        self.btn_reset.clicked.connect(self.reset_logic)
        
        al.addWidget(self.btn_add_reader)
        al.addWidget(self.btn_add_writer)
        al.addWidget(self.btn_reset)
        left_panel.addWidget(act_card)
        
        # 4. Metrics Panel
        metrics_card = GlassCard(lambda: QVBoxLayout())
        ml = metrics_card.layout()
        ml.addWidget(QLabel("Métriques de Performance", styleSheet=f"color:{COLORS['text']}; font-weight:bold;"))
        self.lbl_metrics = QLabel("Attente Moy: 0s\nDébit: 0 ops/s")
        self.lbl_metrics.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 12px; margin-top:5px;")
        ml.addWidget(self.lbl_metrics)
        left_panel.addWidget(metrics_card)
        
        left_panel.addStretch()
        layout.addLayout(left_panel, 1)
        
        # --- CENTER PANEL: Visualization ---
        center_panel = QVBoxLayout()
        
        self.visualizer = ResourceVisualizer()
        center_panel.addWidget(self.visualizer, 2)
        
        # Queues
        q_card = GlassCard(lambda: QVBoxLayout())
        ql = q_card.layout()
        ql.addWidget(QLabel("État des Processus", styleSheet=f"color:{COLORS['text_dim']}; font-weight:bold"))
        self.lbl_queue = QLabel()
        self.lbl_queue.setWordWrap(True)
        # Use a monospace font for better alignment if needed, or stick to clean UI
        self.lbl_queue.setStyleSheet(f"font-size: 13px; color: {COLORS['text']}; margin-top: 10px; line-height: 1.4;")
        ql.addWidget(self.lbl_queue)
        center_panel.addWidget(q_card, 1)
        
        layout.addLayout(center_panel, 2)
        
        # --- RIGHT PANEL: Logs ---
        right_panel = QVBoxLayout()
        self.log_widget = QTextEdit()
        self.log_widget.setReadOnly(True)
        self.log_widget.setStyleSheet(f"""
            background-color: {COLORS['card_bg']};
            color: {COLORS['text_dim']};
            border: none;
            border-radius: 12px;
            padding: 10px;
            font-family: Consolas, monospace;
            font-size: 12px;
        """)
        right_panel.addWidget(QLabel("Historique des Événements", styleSheet=f"color:{COLORS['text']}; font-weight:bold;"))
        right_panel.addWidget(self.log_widget)
        layout.addLayout(right_panel, 1)

    def set_mode(self, mode):
        if self.mode != mode:
            self.mode = mode
            self.reset_logic()

    def add_reader(self):
        self.create_worker(False)

    def add_writer(self):
        self.create_worker(True)

    def create_worker(self, is_writer):
        pid = self.next_id
        self.next_id += 1
        
        # Cleanup fn
        def on_finish(tid, wait_time):
            # Record metrics
            key = 'W_wait' if is_writer else 'R_wait'
            self.metrics[key].append(wait_time)
            self.metrics['ops_count'] += 1
        
        w = GenericWorker(pid, self.lock, self.shared_data, (2.0, 4.0), is_writer)
        w.signals.log.connect(self.log_system)
        w.signals.finished_ok.connect(on_finish)
        
        self.threads.append(w)
        w.start()

    def log_system(self, msg, type="info"):
        color = {
            "write": COLORS['red'],
            "read": COLORS['accent'],
            "wait": "#fbbf24",
            "active": COLORS['green'],
            "free": COLORS['text_dim']
        }.get(type, "white")
        
        t = datetime.now().strftime("%H:%M:%S")
        self.log_widget.append(f'<div style="margin-bottom:2px;"><span style="color:#64748b;">[{t}]</span> <span style="color:{color};">{msg}</span></div>')

    def update_stats(self):
        # Clean up finished threads
        # We assume if isFinished() is true, it is safe to drop reference
        self.threads = [t for t in self.threads if t.isRunning()]

        # Build lists based on Worker state
        waiting_r = []
        waiting_w = []
        active_r_list = []
        active_w_id = None
        
        for t in self.threads:
            p_str = f"{'W' if t.is_writer else 'R'}{t.t_id}"
            if t.current_state == "waiting":
                if t.is_writer: waiting_w.append(p_str)
                else: waiting_r.append(p_str)
            elif t.current_state == "active":
                if t.is_writer: active_w_id = t.t_id
                else: active_r_list.append(t.t_id)

        # Update Text
        def fmt_list(l): return ', '.join(l) if l else 'None'
        
        txt = (
            f"<b>ACTIF:</b><br/>"
            f"Rédacteur: <span style='color:{COLORS['red']}'>{'W'+str(active_w_id) if active_w_id else 'None'}</span><br/>"
            f"Lecteurs : <span style='color:{COLORS['accent']}'>{[f'R{i}' for i in active_r_list]}</span><br/><br/>"
            f"<b>EN ATTENTE:</b><br/>"
            f"Rédacteurs: <span style='color:#fbbf24'>{fmt_list(waiting_w)}</span><br/>"
            f"Lecteurs : <span style='color:#fbbf24'>{fmt_list(waiting_r)}</span>"
        )
        self.lbl_queue.setText(txt)
        
        # Calculate Metrics
        elapsed = time.time() - self.metrics['start_time']
        throughput = self.metrics['ops_count'] / elapsed if elapsed > 0 else 0
        
        all_waits = self.metrics['R_wait'] + self.metrics['W_wait']
        avg_wait = sum(all_waits) / len(all_waits) if all_waits else 0.0
        max_wait = max(all_waits) if all_waits else 0.0
        
        m_txt = (
            f"Débit Total: {throughput:.2f} ops/s<br/>"
            f"Attente Moy: {avg_wait:.2f}s (Max: {max_wait:.2f}s)<br/>"
            f"Wait Reads: {len(self.metrics['R_wait'])} | Wait Writes: {len(self.metrics['W_wait'])}"
        )
        self.lbl_metrics.setText(m_txt)
        
        # Update Visualizer
        self.visualizer.update_state(active_r_list, active_w_id, self.shared_data['val'])



if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
