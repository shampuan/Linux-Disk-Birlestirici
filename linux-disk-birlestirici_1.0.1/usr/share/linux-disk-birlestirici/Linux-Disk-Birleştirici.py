#!/usr/bin/env python3

import sys
import subprocess
import os
import json
import re

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QMessageBox,
    QProgressBar, QTextEdit, QFrame, QSizePolicy, QSpacerItem
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QUrl, QSize
from PyQt5.QtGui import QPainter, QColor, QPen, QIcon, QMovie, QPixmap
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent, QMediaPlaylist

# Renk kodları sözlüğü
COLOR_SCHEME = {
    "empty": QColor(Qt.white),            # Boş yerler
    "metadata": QColor(128, 0, 128),      # Mor - Metaveriler (MFT benzeri)
    "non_fragmented": QColor(0, 100, 0), # Koyu Yeşil - Parçalanmamış dosyalar
    "fragmented": QColor(255, 99, 71),   # Açık Kırmızı - Parçalanmış dosyalar (Domates Kırmızısı)
    "unmovable": QColor(64, 64, 64),      # Koyu Gri - Taşınmaması gereken dosyalar
    "unknown": QColor(192, 192, 192)      # Gri - Bilinmeyen durumlar / Başlangıç durumu
}

# Disk Haritası Çizimi için Özel Widget
class DiskMapWidget(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setFrameShadow(QFrame.Sunken)
        self.setMinimumSize(400, 200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.block_size = 10 # Her bloğun piksel boyutu
        self.disk_map_data = [] # Burası gerçek blok verileriyle dolacak (şimdilik simülasyon)
        self.cols = 0
        self.rows = 0

        # Birleştirme skoru tabanlı temsili harita için
        self.fragmentation_score = -1 # -1 başlangıç durumunu temsil eder
        self.generate_dummy_map_data() # Başlangıçta haritayı oluştur
        self.update()

    def set_fragmentation_score(self, score):
        self.fragmentation_score = score
        self.generate_dummy_map_data() # Skor değiştikçe temsili haritayı güncelle
        self.update() # Widget'ı yeniden çiz

    def generate_dummy_map_data(self):
        widget_width = self.width() - self.lineWidth() * 2
        widget_height = self.height() - self.lineWidth() * 2

        if widget_width <= 0 or widget_height <= 0:
            self.cols = 0
            self.rows = 0
            self.disk_map_data = []
            return

        self.cols = widget_width // self.block_size
        self.rows = widget_height // self.block_size
        total_blocks = self.cols * self.rows

        if total_blocks == 0:
            self.disk_map_data = []
            return

        self.disk_map_data = []
        
        if self.fragmentation_score == -1:
            # Başlangıç durumu: Tüm bloklar açık gri (unknown)
            self.disk_map_data = [[COLOR_SCHEME["unknown"]] * self.cols for _ in range(self.rows)]
            return

        # Parçalanma oranına göre renk dağılımı
        fragmented_ratio = 0.0
        if self.fragmentation_score == 0:
            fragmented_ratio = 0.0 # Skor 0 ise kırmızı blok gösterme
        elif 1 <= self.fragmentation_score <= 30:
            fragmented_ratio = 0.1
        elif 31 <= self.fragmentation_score <= 55:
            fragmented_ratio = 0.4
        elif self.fragmentation_score >= 56:
            fragmented_ratio = 0.7

        num_fragmented = int(total_blocks * fragmented_ratio)
        
        # Kalan blokların oranını %80 dolu, %10 boş, %5 metadata, %5 taşınamaz şeklinde dağıtalım
        empty_ratio = 0.10
        metadata_ratio = 0.05
        unmovable_ratio = 0.05
        
        # Non-fragmented oranı kalan boşluğu dolduracak
        num_non_fragmented = total_blocks - num_fragmented - int(total_blocks * empty_ratio) - int(total_blocks * metadata_ratio) - int(total_blocks * unmovable_ratio)
        if num_non_fragmented < 0:
            num_non_fragmented = 0
        
        num_empty = int(total_blocks * empty_ratio)
        num_metadata = int(total_blocks * metadata_ratio)
        num_unmovable = int(total_blocks * unmovable_ratio)

        block_types = (
            [COLOR_SCHEME["fragmented"]] * num_fragmented +
            [COLOR_SCHEME["non_fragmented"]] * num_non_fragmented +
            [COLOR_SCHEME["empty"]] * num_empty +
            [COLOR_SCHEME["metadata"]] * num_metadata +
            [COLOR_SCHEME["unmovable"]] * num_unmovable
        )
        
        remaining_blocks = total_blocks - len(block_types)
        if remaining_blocks > 0:
            block_types.extend([COLOR_SCHEME["unknown"]] * remaining_blocks)

        import random
        random.shuffle(block_types)

        self.disk_map_data = []
        for i in range(self.rows):
            row_data = []
            for j in range(self.cols):
                if block_types:
                    row_data.append(block_types.pop(0))
                else:
                    row_data.append(COLOR_SCHEME["unknown"])
            self.disk_map_data.append(row_data)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        offset_x = self.lineWidth()
        offset_y = self.lineWidth()

        if not self.disk_map_data:
            painter.drawText(self.rect(), Qt.AlignCenter, "Disk Haritası Verisi Yok / Yükleniyor...")
            return

        for r in range(self.rows):
            for c in range(self.cols):
                color = self.disk_map_data[r][c]
                painter.setBrush(QColor(color))
                painter.setPen(QPen(Qt.gray, 0.5))

                x = offset_x + c * self.block_size
                y = offset_y + r * self.block_size
                painter.drawRect(x, y, self.block_size, self.block_size)

        painter.end()

    def resizeEvent(self, event):
        self.generate_dummy_map_data()
        self.update()


# Disk birleştirme işlemini ayrı bir thread'de çalıştırmak için
class DefragWorker(QThread):
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    progress = pyqtSignal(int)

    def __init__(self, device_path, map_widget):
        super().__init__()
        self.device_path = device_path
        self.map_widget = map_widget
        self.is_running = True

    def run(self):
        try:
            # Simülasyon: Birleştirme başlamadan önce haritayı güncelle (yüksek parçalanma göster)
            self.progress.emit(10)
            self.map_widget.set_fragmentation_score(90)
            self.msleep(500)

            # Gerçek e4defrag komutu
            command = ["pkexec", "/usr/sbin/e4defrag", self.device_path]
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            
            # Gerçek çıktı olmadığı için ilerlemeyi simüle edelim
            for i in range(10):
                if not self.is_running: return
                self.progress.emit(10 + i * 8)
                self.msleep(500)

            stdout, stderr = process.communicate()

            if process.returncode == 0:
                self.progress.emit(100)
                self.finished.emit(f"Disk birleştirme işlemi başarıyla tamamlandı: {self.device_path}\n\n{stdout}")
            else:
                self.error.emit(f"Disk birleştirme işlemi sırasında bir hata oluştu:\n\n{stderr}")

        except Exception as e:
            self.error.emit(f"Beklenmedik bir hata oluştu: {e}")
        finally:
            if self.isFinished() or (hasattr(process, 'returncode') and process.returncode == 0):
                self.map_widget.set_fragmentation_score(0) # Başarılıysa skoru 0 yap
            else:
                self.map_widget.set_fragmentation_score(self.map_widget.fragmentation_score) # Hata veya yarım kalırsa son skoru koru
            self.is_running = False


    def terminate(self):
        self.is_running = False
        super().terminate()


# e4defrag kontrolü için ayrı bir worker (Analiz Et butonu için)
class CheckDefragWorker(QThread):
    finished = pyqtSignal(int, str)
    error = pyqtSignal(str)

    def __init__(self, device_path):
        super().__init__()
        self.device_path = device_path

    def run(self):
        try:
            command = ["pkexec", "/usr/sbin/e4defrag", "-c", self.device_path]
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            stdout, stderr = process.communicate()

            if process.returncode == 0:
                score = -1
                lines = stdout.splitlines()

                found_score_line = False
                for line in lines:
                    if "Fragmentation score" in line:
                        match = re.search(r'Fragmentation score\s*(\d+)', line)
                        if match:
                            score = int(match.group(1))
                            found_score_line = True
                    if "No fragmentation found" in line and not found_score_line:
                        score = 0

                self.finished.emit(score, stdout)
            else:
                self.error.emit(f"Disk parçalanma kontrolü sırasında bir hata oluştu:\n{stderr}")

        except FileNotFoundError:
            self.error.emit("Hata: '/usr/sbin/e4defrag' komutu bulunamadı. Lütfen 'e2fsprogs' paketinin kurulu olduğundan emin olun.")
        except Exception as e:
            self.error.emit(f"Parçalanma kontrolü sırasında beklenmedik bir hata oluştu: {e}")

class DiskDefragmenterApp(QWidget):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.check_worker = None
        self.media_player = QMediaPlayer() # Medya oynatıcı objesi
        self.playlist = QMediaPlaylist() # Playlist objesi
        self.movie = None # QMovie objesi
        self.initUI()

    def initUI(self):
        self.setWindowTitle('Linux Disk Fragmenter')
        current_dir = os.path.dirname(os.path.abspath(__file__))
        
        icon_path = os.path.join(current_dir, 'fragmenter.png')
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.setGeometry(300, 300, 800, 600)

        main_layout = QVBoxLayout()

        disk_selection_layout = QHBoxLayout()
        disk_label = QLabel('Disk Seçin:')
        self.disk_combobox = QComboBox()
        disk_selection_layout.addWidget(disk_label)
        disk_selection_layout.addWidget(self.disk_combobox)
        main_layout.addLayout(disk_selection_layout)

        # Butonlar için yatay düzen
        button_layout = QHBoxLayout()
        self.analyze_button = QPushButton('Analiz Et')
        self.analyze_button.clicked.connect(self.start_analysis)
        button_layout.addWidget(self.analyze_button)

        self.defrag_button = QPushButton('Birleştir')
        self.defrag_button.clicked.connect(self.start_defrag)
        self.defrag_button.setEnabled(False)
        button_layout.addWidget(self.defrag_button)

        self.about_button = QPushButton('Hakkında')
        self.about_button.clicked.connect(self.show_about)
        button_layout.addWidget(self.about_button)
        main_layout.addLayout(button_layout) # Buton düzenini ana düzene ekle

        # Bilgi etiketlerini ve resim etiketini burada tanımlıyoruz
        self.info_label = QLabel("Seçilen disk hakkında bilgi burada görünecektir.")
        self.info_label.setWordWrap(True)

        self.defrag_result_label = QLabel("")
        self.defrag_result_label.setWordWrap(True)
        self.defrag_result_label.setStyleSheet("font-weight: bold; color: green;")

        self.image_display_label = QLabel()
        self.image_display_label.setAlignment(Qt.AlignCenter)
        self.image_display_label.setScaledContents(True)
        self.image_display_label.setFixedSize(QSize(85, 122)) 
        self.image_display_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.load_initial_image() # Başlangıçta 1.png'yi yükle

        # Bilgi etiketleri için dikey düzen
        info_vertical_layout = QVBoxLayout()
        info_vertical_layout.addSpacing(15) # Butonlar ile bilgi arasında boşluk
        info_vertical_layout.addWidget(self.info_label)
        info_vertical_layout.addWidget(self.defrag_result_label)
        # info_vertical_layout.addStretch(1) # Bu satır boşluğu minimize etmek için kaldırıldı.

        # Bilgi etiketleri ve resim için ana yatay düzen
        info_and_image_container_layout = QHBoxLayout()
        info_and_image_container_layout.addLayout(info_vertical_layout)
        info_and_image_container_layout.addWidget(self.image_display_label, alignment=Qt.AlignRight | Qt.AlignTop) # Resmi sağa ve üste hizala
        main_layout.addLayout(info_and_image_container_layout) # Ana düzene ekle
        main_layout.addSpacing(10) # Bilgi/Resim alanı ile Disk Haritası arasında küçük bir boşluk

        # Disk Haritası Bölümü
        disk_map_group_box = QVBoxLayout()
        disk_map_label = QLabel("<b>Disk Haritası</b>")
        disk_map_label.setAlignment(Qt.AlignCenter)
        disk_map_group_box.addWidget(disk_map_label)
        
        self.disk_map_widget = DiskMapWidget(self)
        disk_map_group_box.addWidget(self.disk_map_widget)
        main_layout.addLayout(disk_map_group_box) # Ana düzene ekle

        # Renk Anahtarı (Legend)
        legend_layout = QHBoxLayout()
        self.add_legend_item(legend_layout, "Boş Yerler", "Diskteki kullanılabilir boş alan", COLOR_SCHEME["empty"])
        self.add_legend_item(legend_layout, "Meta Veriler", "Dosya sistemi yapıları ve indeksler", COLOR_SCHEME["metadata"])
        self.add_legend_item(legend_layout, "Parçalanmamış", "Düzgün yerleşmiş dosya parçaları", COLOR_SCHEME["non_fragmented"])
        self.add_legend_item(legend_layout, "Parçalanmış", "Dağınık dosya parçaları, birleştirilmeli", COLOR_SCHEME["fragmented"])
        self.add_legend_item(legend_layout, "Taşınamaz", "Sistem veya kullanıcı tarafından kilitlenmiş alanlar", COLOR_SCHEME["unmovable"])
        self.add_legend_item(legend_layout, "Bilinmeyen/Boş", "Durumu bilinmeyen veya başlangıç bloğu", COLOR_SCHEME["unknown"])
        main_layout.addLayout(legend_layout) # Ana düzene ekle

        self.populate_disks()
        self.disk_combobox.currentIndexChanged.connect(self.on_disk_selection_changed)
        self.on_disk_selection_changed()

        self.setLayout(main_layout)

    def load_initial_image(self):
        current_dir = os.path.dirname(os.path.abspath(__file__))
        image_path = os.path.join(current_dir, '1.png')

        # Clear any existing movie or pixmap to ensure a clean slate
        if self.movie and self.movie.state() == QMovie.Running:
            self.movie.stop()
        self.image_display_label.setMovie(None) # Detach movie from label if any
        self.image_display_label.clear() # Clear any pixmap content from label

        pixmap = QPixmap(image_path)
        if not pixmap.isNull():
            self.image_display_label.setPixmap(pixmap)
        else:
            self.image_display_label.setText("Image not found: 1.png")

    def start_operation_animation(self):
        current_dir = os.path.dirname(os.path.abspath(__file__))
        gif_path = os.path.join(current_dir, '2.gif')

        # Stop and clear any existing movie/pixmap
        if self.movie and self.movie.state() == QMovie.Running:
            self.movie.stop()
        self.image_display_label.setMovie(None) # Detach movie from label if any
        self.image_display_label.clear() # Clear any pixmap content from label

        self.movie = QMovie(gif_path)
        if self.movie.isValid():
            self.movie.setCacheMode(QMovie.CacheAll)
            self.image_display_label.setMovie(self.movie)
            self.movie.start()
        else:
            self.image_display_label.setText("Animation not found or invalid: 2.gif. Showing default image.")
            self.load_initial_image()

    def stop_operation_animation(self):
        if self.movie and self.movie.state() == QMovie.Running:
            self.movie.stop()
        self.image_display_label.clear()
        self.load_initial_image()

    def add_legend_item(self, layout, text, tooltip, color):
        color_label = QLabel()
        color_label.setFixedSize(20, 15)
        color_label.setStyleSheet(f"background-color: {color.name()}; border: 1px solid gray;")
        
        text_label = QLabel(text)
        text_label.setToolTip(tooltip)
        
        item_layout = QHBoxLayout()
        item_layout.addWidget(color_label)
        item_layout.addWidget(text_label)
        item_layout.addStretch(1)
        layout.addLayout(item_layout)

    def show_about(self):
        about_text = (
            "Linux Disk Fragmenter\n"
            "Sürüm: 1.0.2\n"
            "Author: A. Serhat KILIÇOĞLU\n"
            "Github: https://github.com/shampuan\n\n"
            "Bu program, EXT4 dosya sistemine sahip disklerde parçalanmayı analiz eder "
            "ve disk birleştirme işlemi yapmanızı sağlar."
        )
        QMessageBox.information(self, "Hakkında", about_text)

    def play_background_music(self):
        current_dir = os.path.dirname(os.path.abspath(__file__))
        music_path = os.path.join(current_dir, 'Open Those Bright Eyes.mp3')
        
        if os.path.exists(music_path):
            self.playlist.clear()
            self.playlist.addMedia(QMediaContent(QUrl.fromLocalFile(music_path)))
            self.playlist.setPlaybackMode(QMediaPlaylist.Loop) # Müziğin sürekli tekrar etmesini sağlar
            self.media_player.setPlaylist(self.playlist)
            self.media_player.setVolume(50)
            self.media_player.play()
        else:
            pass

    def stop_background_music(self):
        if self.media_player.state() == QMediaPlayer.PlayingState:
            self.media_player.stop()

    def populate_disks(self):
        try:
            command = ["lsblk", "-o", "NAME,FSTYPE,MOUNTPOINT,PATH", "--json"]
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            disk_info = json.loads(result.stdout)

            self.disks = []
            self.disk_combobox.clear()

            for device in disk_info.get("blockdevices", []):
                if "children" in device:
                    for child_device in device["children"]:
                        self._add_disk_item(child_device)
                else:
                    self._add_disk_item(device)

            if not self.disks:
                self.disk_combobox.addItem("Disk bulunamadı veya yetersiz yetki.")
                self.analyze_button.setEnabled(False)
                self.defrag_button.setEnabled(False)
                self.info_label.setText("Sistemde birleştirilebilecek EXT4 disk bulunamadı veya yetkisizlik.")
            else:
                self.analyze_button.setEnabled(True)
                self.defrag_button.setEnabled(False)
                self.on_disk_selection_changed()

        except FileNotFoundError:
            QMessageBox.critical(self, "Hata", "'lsblk' komutu bulunamadı. Lütfen 'util-linux' paketinin kurulu olduğundan emin olun.")
            self.analyze_button.setEnabled(False)
            self.defrag_button.setEnabled(False)
        except Exception as e:
            QMessageBox.critical(self, "Hata", f"Diskler listelenirken beklenmedik bir hata oluştu: {e}")
            self.analyze_button.setEnabled(False)
            self.defrag_button.setEnabled(False)

    def _add_disk_item(self, device):
        fstype = device.get("fstype")
        mountpoint = device.get("mountpoint")
        path = device.get("path")

        if fstype and path and path.startswith("/dev/"):
            display_name = f"{path} ({fstype})"
            if mountpoint:
                display_name += f" - {mountpoint}"

            self.disks.append({
                "path": path,
                "fstype": fstype,
                "mountpoint": mountpoint if mountpoint else "Yok"
            })
            self.disk_combobox.addItem(display_name)

    def on_disk_selection_changed(self):
        selected_index = self.disk_combobox.currentIndex()
        self.defrag_result_label.clear()
        self.disk_map_widget.set_fragmentation_score(-1)
        self.stop_operation_animation()

        if selected_index == -1 or not self.disks or selected_index >= len(self.disks):
            self.info_label.setText("Lütfen geçerli bir disk seçin.")
            self.analyze_button.setEnabled(False)
            self.defrag_button.setEnabled(False)
            return

        selected_disk_info = self.disks[selected_index]
        fstype = selected_disk_info["fstype"]
        path = selected_disk_info["path"]
        mountpoint = selected_disk_info["mountpoint"]

        if fstype == "ext4":
            self.info_label.setText(
                f"Seçilen disk: <b>{path}</b><br>"
                f"Dosya Sistemi: <b>{fstype}</b><br>"
                f"Bağlama Noktası: <b>{mountpoint}</b><br><br>"
                f"<b>Bu disk birleştirilebilir türden.</b> Analiz etmek için 'Analiz Et' butonuna tıklayın."
            )
            self.analyze_button.setEnabled(True)
            self.defrag_button.setEnabled(False)
        else:
            self.info_label.setText(
                f"Seçilen disk: <b>{path}</b><br>"
                f"Dosya Sistemi: <b>{fstype}</b><br>"
                f"Bağlama Noktası: <b>{mountpoint}</b><br><br>"
                f"<span style='color:red;'><b>Uyarı:</b> Linux ortamında bu diske doğrudan disk birleştirme işlemi yapılamaz.</span>"
            )
            self.analyze_button.setEnabled(False)
            self.defrag_button.setEnabled(False)

    def start_analysis(self):
        selected_index = self.disk_combobox.currentIndex()
        if selected_index == -1 or not self.disks or selected_index >= len(self.disks):
            QMessageBox.warning(self, "Uyarı", "Lütfen bir disk seçin.")
            return

        selected_disk_info = self.disks[selected_index]
        fstype = selected_disk_info["fstype"]
        path = selected_disk_info["path"]

        if fstype != "ext4":
            QMessageBox.warning(self, "Hata", f"Seçilen disk ({path}) EXT4 formatında değil. Sadece EXT4 diskler birleştirilebilir.")
            return

        self.analyze_button.setEnabled(False)
        self.defrag_button.setEnabled(False)
        self.disk_combobox.setEnabled(False)

        self.start_operation_animation()
        QMessageBox.information(self, "Analiz Başlatılıyor", "Disk analiz ediliyor... (Şifre sorabilir)")
        self.defrag_result_label.clear()
        
        self.play_background_music()

        self.check_worker = CheckDefragWorker(path)
        self.check_worker.finished.connect(
            lambda score, full_output: self.display_defrag_score(path, fstype, selected_disk_info["mountpoint"], score, full_output)
        )
        self.check_worker.error.connect(self.display_defrag_check_error)
        self.check_worker.start()

    def display_defrag_score(self, path, fstype, mountpoint, score, full_output):
        self.analyze_button.setEnabled(True)
        self.disk_combobox.setEnabled(True)
        self.defrag_button.setEnabled(True)
        
        self.stop_background_music()
        self.stop_operation_animation()

        current_info_text = (
            f"Seçilen disk: <b>{path}</b><br>"
            f"Dosya Sistemi: <b>{fstype}</b><br>"
            f"Bağlama Noktası: <b>{mountpoint}</b><br><br>"
            f"<b>Bu disk birleştirilebilir türden.</b>"
        )
        self.info_label.setText(current_info_text)

        result_text = ""
        if score == 0:
            result_text = "Parçalanma Puanı: <b>0</b>. Disk parçalanmış değil. Birleştirmeye gerek yok."
            self.defrag_result_label.setStyleSheet("font-weight: bold; color: green;")
        elif 1 <= score <= 30:
            result_text = f"Parçalanma Puanı: <b>{score}</b>. Çok az miktarda parçalanma var. Birleştirmeye genellikle gerek yoktur."
            self.defrag_result_label.setStyleSheet("font-weight: bold; color: blue;")
        elif 31 <= score <= 55:
            result_text = f"Parçalanma Puanı: <b>{score}</b>. Orta derecede parçalanmış. Birleştirme önerilir."
            self.defrag_result_label.setStyleSheet("font-weight: bold; color: orange;")
        elif score >= 56:
            result_text = f"Parçalanma Puanı: <b>{score}</b>. Yüksek derecede parçalanma var! Disk birleştirmeye ihtiyaç duyuyor."
            self.defrag_result_label.setStyleSheet("font-weight: bold; color: red;")
        else:
            result_text = "Parçalanma Puanı belirlenemedi veya bilinmeyen bir durum oluştu."
            self.defrag_result_label.setStyleSheet("font-weight: bold; color: gray;")
            self.defrag_button.setEnabled(False)

        self.defrag_result_label.setText(result_text)
        QMessageBox.information(self, "Analiz Sonucu", "Disk analizi tamamlandı.")
        self.disk_map_widget.set_fragmentation_score(score)

    def display_defrag_check_error(self, message):
        selected_index = self.disk_combobox.currentIndex()
        selected_disk_info = self.disks[selected_index]
        path = selected_disk_info["path"]
        fstype = selected_disk_info["fstype"]
        mountpoint = selected_disk_info["mountpoint"]

        self.info_label.setText(
            f"Seçilen disk: <b>{path}</b><br>"
            f"Dosya Sistemi: <b>{fstype}</b><br>"
            f"Bağlama Noktası: <b>{mountpoint}</b><br><br>"
            f"<span style='color:red;'><b>Parçalanma kontrolü sırasında bir hata oluştu: {message}</b></span>"
        )
        self.analyze_button.setEnabled(True)
        self.defrag_button.setEnabled(False)
        self.disk_combobox.setEnabled(True)
        QMessageBox.critical(self, "Hata", f"Parçalanma kontrolü sırasında bir hata oluştu: {message}")
        self.defrag_result_label.clear()
        self.disk_map_widget.set_fragmentation_score(-1)
        self.stop_background_music()
        self.stop_operation_animation()

    def start_defrag(self):
        selected_index = self.disk_combobox.currentIndex()
        if selected_index == -1 or not self.disks or selected_index >= len(self.disks):
            QMessageBox.warning(self, "Uyarı", "Lütfen bir disk seçin.")
            return

        selected_disk_info = self.disks[selected_index]
        device_path = selected_disk_info["path"]
        fstype = selected_disk_info["fstype"]

        if fstype != "ext4":
            QMessageBox.warning(self, "Hata", f"Seçilen disk ({device_path}) EXT4 formatında değil. Sadece EXT4 diskler birleştirilebilir.")
            return

        reply = QMessageBox.question(self, 'Onay',
                                     f'"{device_path}" üzerindeki disk birleştirme işlemini başlatmak istediğinizden emin misiniz? Bu işlem biraz zaman alabilir ve sistem kaynaklarını kullanabilir.',
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

        if reply == QMessageBox.Yes:
            self.defrag_button.setEnabled(False)
            self.analyze_button.setEnabled(False)
            self.disk_combobox.setEnabled(False)

            self.start_operation_animation()
            QMessageBox.information(self, "Birleştirme Başlatılıyor", f"Disk birleştirme işlemi başlatılıyor: <b>{device_path}</b>\n (Şifre sorabilir)")
            
            self.play_background_music()

            self.worker = DefragWorker(device_path, self.disk_map_widget)
            self.worker.finished.connect(self.defrag_finished)
            self.worker.error.connect(self.defrag_error)
            
            self.worker.start()

    def defrag_finished(self, message):
        QMessageBox.information(self, "Başarılı", "Disk birleştirme işlemi tamamlandı.")
        self.reset_ui()
        self.stop_background_music()
        self.stop_operation_animation()

    def defrag_error(self, message):
        QMessageBox.critical(self, "Hata", "Disk birleştirme işlemi sırasında bir hata oluştu.")
        self.reset_ui()
        self.stop_background_music()
        self.stop_operation_animation()

    def closeEvent(self, event):
        is_worker_running = self.worker and self.worker.isRunning()
        is_check_worker_running = self.check_worker and self.check_worker.isRunning()

        if is_worker_running or is_check_worker_running:
            reply = QMessageBox.question(self, 'Uyarı',
                                         "Devam eden bir işlem var (analiz veya birleştirme). Uygulamayı şimdi kapatmak veri kaybına neden olabilir veya işlemi kesintiye uğratabilir. Yine de kapatmak istiyor musunuz?",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                if is_worker_running:
                    self.worker.terminate()
                    self.worker.wait(5000)
                if is_check_worker_running:
                    self.check_worker.terminate()
                    self.check_worker.wait(5000)
                self.stop_background_music()
                self.stop_operation_animation()
                event.accept()
            else:
                event.ignore()
        else:
            self.stop_background_music()
            self.stop_operation_animation()
            event.accept()

    def reset_ui(self):
        self.defrag_button.setEnabled(False)
        self.analyze_button.setEnabled(True)
        self.disk_combobox.setEnabled(True)
        self.worker = None
        self.check_worker = None
        self.on_disk_selection_changed()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = DiskDefragmenterApp()
    ex.show()
    sys.exit(app.exec_())
