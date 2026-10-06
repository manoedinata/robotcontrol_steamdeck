# MANUAL BOOK
## Aplikasi Kontrol Robot — Steam Deck
### Bagian Kontrol & Pengaturan (Software)

> **Draft v0.1** — dokumen ini adalah draf kerja untuk direview sebelum difinalisasi. Mengikuti struktur "Manual Book Robot Inspeksi Bagian Dalam Pipa" sebagai acuan format, disesuaikan dengan aplikasi kontrol robot di Steam Deck (Electron + Vue, backend FastAPI).

---

## DAFTAR ISI

- Bagian 1: Pendahuluan
- Bagian 2: Memulai Aplikasi
  - 2.1 Persyaratan
  - 2.2 Menjalankan Aplikasi
  - 2.3 Menghubungkan Gamepad
- Bagian 3: Mengenal Tampilan Utama (Home)
  - 3.1 Bilah Telemetri Atas
  - 3.2 Panel Kamera dan Overlay
  - 3.3 Tombol Layang (Shell Actions)
  - 3.4 Jarak Tempuh (Odometry)
- Bagian 4: Mengendalikan Robot
  - 4.1 Kontrol Gerak (Drive)
  - 4.2 Mode Maju / Mundur
  - 4.3 Mengganti Sumber Kamera
- Bagian 5: Kontrol Kamera PTZ
  - 5.1 Pan / Tilt / Zoom
  - 5.2 Fokus Kamera
  - 5.3 Lampu Inframerah
  - 5.4 Kecepatan Pan/Tilt
- Bagian 6: Pengaturan (Settings)
  - 6.1 Membuka dan Menutup Pengaturan
  - 6.2 Camera Feeds
  - 6.3 UDP Destination
  - 6.4 Robot Controls (Batas Kecepatan)
  - 6.5 Camera Rotation (PTZ)
  - 6.6 Recording (Lokasi Penyimpanan)
  - 6.7 Text Input (Keyboard Layar)
- Bagian 7: Perekaman dan Pemutaran Ulang
  - 7.1 Memulai dan Menghentikan Rekaman
  - 7.2 Membuka Halaman Recordings
  - 7.3 Memutar, Menyimpan, dan Menghapus Rekaman
- Bagian 8: Navigasi Antarmuka dengan Gamepad
- Bagian 9: Indikator Status dan Koneksi
- Bagian 10: Mengakhiri Sesi

---

## Bagian 1: Pendahuluan

Aplikasi ini adalah perangkat lunak kontrol untuk robot yang dijalankan pada Steam Deck, terdiri dari aplikasi desktop (Electron + Vue) sebagai antarmuka operator dan backend (FastAPI) yang menangani koneksi kamera, kontrol UDP ke robot, dan perekaman. Operator mengendalikan gerak robot dan kamera PTZ (pan/tilt/zoom) secara real-time, memantau telemetri, serta merekam dan memutar ulang hasil inspeksi.

Dokumen ini membahas **sisi perangkat lunak saja**: cara mengoperasikan kontrol di layar/gamepad dan cara mengatur konfigurasi aplikasi (Settings). Perakitan, kelistrikan, dan komponen fisik robot tidak dibahas di sini.

---

## Bagian 2: Memulai Aplikasi

### 2.1 Persyaratan

a. Steam Deck (atau PC) dengan aplikasi kontrol sudah terpasang.
b. Backend robot (FastAPI) berjalan dan dapat diakses — secara default di `http://127.0.0.1:8000` ketika dijalankan melalui Docker/Steam.
c. Gamepad terhubung (opsional untuk navigasi layar, wajib untuk kontrol gerak dan PTZ dengan tombol fisik).
d. Robot menyala dan berada pada jaringan yang sama dengan Steam Deck (untuk UDP dan kamera).

### 2.2 Menjalankan Aplikasi

a. Jalankan aplikasi dari launcher Steam atau ikon aplikasi.
b. Tunggu hingga aplikasi menampilkan tampilan utama (Home) dengan panel kamera.
c. Jika backend belum siap, tunggu beberapa saat — aplikasi akan mencoba menyambung ulang secara otomatis.

### 2.3 Menghubungkan Gamepad

a. Sambungkan gamepad (kabel atau Bluetooth) sebelum atau sesudah aplikasi berjalan.
b. Gerakkan stick atau tekan tombol apa pun agar peramban mendeteksi gamepad (ketentuan standar Gamepad API).
c. Status gamepad terbaca di panel kontrol pada tampilan Home.

---

## Bagian 3: Mengenal Tampilan Utama (Home)

### 3.1 Bilah Telemetri Atas

Menampilkan:
- Status koneksi backend/robot.
- Alamat kamera yang sedang aktif.
- Slider kecepatan pan/tilt (1x–6x), hanya muncul bila alamat PTZ cocok dengan kamera yang ditampilkan.
- Jarak tempuh (odometry), di kanan slider kecepatan.
- Indikator baterai (lihat 3.4), di pojok kanan atas — dapat ditekan untuk beralih antara baterai robot dan baterai Steam Deck.

### 3.2 Panel Kamera dan Overlay

a. Video dari sumber kamera aktif ditampilkan penuh di panel utama.
b. Garis panduan tengah (center guides) ditampilkan transparan di atas video sebagai bantuan visual saat menyusuri pipa — bersifat dekoratif, tidak dapat disentuh.
c. Jika koneksi kamera gagal, muncul status error dengan tombol "Hubungkan lagi"; aplikasi tetap mencoba menyambung ulang otomatis setiap beberapa detik.

### 3.3 Tombol Layang (Shell Actions)

Di sisi kanan layar (dari atas ke bawah):
- **Record** — mulai/berhenti merekam semua sumber kamera yang terkonfigurasi sekaligus.
- **Recordings** — membuka pustaka hasil rekaman.
- **Exit** — keluar dari aplikasi.
- **Settings** — membuka panel pengaturan.

Di sisi kiri layar (dari atas ke bawah):
- **Focus near / Focus far** — fokus kamera PTZ mendekat/menjauh (hanya tampil bila PTZ aktif pada kamera yang ditampilkan).
- **Lampu inframerah** — menyalakan/mematikan IR illuminator kamera PTZ.
- **Arah gerak (drive direction)** — mengganti mode maju/mundur (lihat 4.2).

### 3.4 Jarak Tempuh (Odometry)

a. Jarak dihitung dari kecepatan dua roda yang dilaporkan robot, diintegrasikan terhadap waktu.
b. Nilai bertanda (signed): mundur akan mengurangi jarak, bukan menambah — jarak menunjukkan posisi relatif terhadap titik awal, bukan odometer total.
c. Tekan dan tahan angka jarak selama ±0,7 detik untuk mereset jarak ke nol.
d. Jarak dihitung dari dua nilai yang diatur di Settings: *metres per encoder count* dan *wheel separation* (lihat 6.3).

---

## Bagian 4: Mengendalikan Robot

### 4.1 Kontrol Gerak (Drive)

| Input | Fungsi |
|---|---|
| Stick kiri, ke atas | Robot bergerak maju (atau mundur jika mode dibalik — lihat 4.2) |
| Stick kiri, ke bawah | Tidak berfungsi — stick tidak memiliki setengah bagian bawah |
| Stick kanan, kiri/kanan | Robot berbelok (theta velocity) |

a. Stick kiri hanya membaca gerakan ke atas; mendorong ke bawah tidak memberi efek apa pun (secara visual, bagian bawah lingkaran joystick di layar juga diredupkan).
b. Stick kanan mengatur kecepatan belok; kiri = belok negatif, kanan = belok positif.
c. Terdapat *dead zone* 0,12 untuk input gamepad fisik; input sentuh/pointer di layar tidak memiliki dead zone.

### 4.2 Mode Maju / Mundur

a. Tekan tombol **arah gerak** di tumpukan ikon kiri layar, atau tombol **A** pada gamepad, untuk beralih antara mode **maju** dan **mundur**.
b. Pada mode maju, dorongan stick ke atas dikirim sebagai kecepatan positif. Pada mode mundur, nilai yang sama dikirim terbalik (negatif).
c. Ikon tombol menunjukkan panah ke atas (maju) atau panah ke bawah terisi (mundur); pembacaan kecepatan linear di layar juga akan bernilai negatif saat mode mundur.
d. Saat mode mundur, arah belok otomatis dibalik agar kemudi tetap relatif terhadap sudut pandang operator, termasuk saat robot berputar di tempat.
e. Mode ini **tidak disimpan** — setiap aplikasi dijalankan ulang, mode kembali ke **maju**.

### 4.3 Mengganti Sumber Kamera

a. Tekan tombol **B** pada gamepad untuk beralih ke sumber kamera berikutnya (hanya aktif bila lebih dari satu kamera dikonfigurasi dan tidak ada panel/dialog lain yang terbuka).
b. Semua sumber kamera yang dikonfigurasi tetap tersambung di latar belakang; berganti kamera tidak menyambungkan ulang koneksi, sehingga perpindahan terasa instan.

---

## Bagian 5: Kontrol Kamera PTZ

> Seluruh kontrol PTZ hanya aktif apabila **alamat IP PTZ** pada Settings (lihat 6.5) sama dengan alamat kamera yang sedang ditampilkan di layar. Jika tidak cocok, tombol fokus dan lampu disembunyikan dan input D-pad/shoulder tidak berpengaruh.

### 5.1 Pan / Tilt / Zoom

| Input | Fungsi |
|---|---|
| D-pad atas | Kamera tilt ke atas |
| D-pad bawah | Kamera tilt ke bawah |
| D-pad kiri/kanan | Kamera pan kiri/kanan |
| LB (tahan) | Zoom out |
| RB (tahan) | Zoom in |

a. Perintah pan/tilt/zoom dikirim selama tombol ditahan dan otomatis berhenti saat dilepas.
b. Tombol bahu (LB/RB) bersifat digital (ditekan/tidak), karena trigger analog tidak selalu terbaca andal di Electron/Chromium.

### 5.2 Fokus Kamera

| Input | Fungsi |
|---|---|
| X (tekan sekilas, <300 ms) | Fokus mendekat satu langkah, berhenti otomatis setelah ±250 ms |
| X (tahan, >300 ms) | Fokus menjauh terus-menerus selama ditahan |
| Tombol layar "Focus near" / "Focus far" | Fokus mendekat/menjauh selama ditekan (tersedia untuk kedua arah secara eksplisit) |

Tombol gamepad (X) hanya menyediakan fokus mendekat sebagai langkah tunggal dan fokus menjauh sebagai tahan kontinu; untuk menyapu fokus mendekat secara kontinu, gunakan tombol layar.

### 5.3 Lampu Inframerah

a. Tekan **Y** pada gamepad, atau tombol lampu inframerah di layar, untuk menyalakan/mematikan lampu IR kamera.
b. Status lampu bersifat *latched* (tetap menyala/mati sampai ditekan lagi) dan tersimpan di sisi robot — status tetap sama meski aplikasi dimuat ulang.
c. Berpindah ke kamera yang alamat PTZ-nya tidak cocok hanya menyembunyikan tombol, **tidak** mematikan lampu — lampu dianggap pengaturan kamera, bukan input yang sedang ditahan.

### 5.4 Kecepatan Pan/Tilt

a. Slider kecepatan pan/tilt berada di bilah atas, di bawah alamat kamera, dengan rentang **1x–6x**.
b. Slider hanya bisa dioperasikan lewat layar sentuh (bukan D-pad, karena D-pad di Home dikhususkan untuk kamera).
c. Perubahan kecepatan berlaku langsung, termasuk saat robot sedang menggerakkan kamera, dan nilai tersimpan otomatis ke pengaturan saat slider dilepas.

---

## Bagian 6: Pengaturan (Settings)

### 6.1 Membuka dan Menutup Pengaturan

a. Tekan tombol **Settings** di tumpukan ikon kanan layar.
b. Panel kontrol kamera dan robot tetap aktif/terpasang di latar belakang; D-pad dan tombol A/B beralih fungsi menjadi navigasi panel (lihat Bagian 8).
c. Tekan **B** atau tombol tutup untuk menutup panel; fokus kembali ke tombol Settings.
d. Selama panel terbuka, seluruh kontrol PTZ dijeda (robot dikirim perintah berhenti) hingga panel ditutup.

### 6.2 Camera Feeds

Untuk setiap sumber kamera (bisa lebih dari satu):

| Kolom | Keterangan |
|---|---|
| Jenis sumber | RTSP atau WebSocket |
| Source IP | Alamat IP kamera |
| Port | Port stream |
| Stream subpath *(opsional)* | Path tambahan, mis. `video` |
| Username / Password *(opsional)* | Kredensial RTSP; password tidak ditampilkan di layar |

a. Tombol **tampilkan kamera** memilih sumber yang aktif ditampilkan di Home.
b. Tombol **hapus kamera** menghapus baris sumber tersebut; minimal satu baris harus tetap ada.
c. **WebRTC backend**: pilih `go2rtc` (default) atau `aiortc`.
d. **RTSP transport**: pilih `TCP` (default) atau `UDP` — menentukan transport saat menyambung RTSP lewat backend `aiortc`; rekaman selalu memakai TCP.

### 6.3 UDP Destination

| Kolom | Keterangan |
|---|---|
| Target host | Alamat IP tujuan perintah UDP ke robot |
| Target port | Port tujuan |
| Telemetry listen port | Port lokal untuk menerima telemetri dari robot |
| Metres per encoder count | Jarak (meter) per satu hitungan encoder — dipakai untuk menghitung jarak tempuh (3.4) |
| Wheel separation (m) | Jarak antar roda kiri-kanan (meter) — dipakai untuk menghitung orientasi pada odometry |

Mengosongkan host atau mengisi port `0` akan menonaktifkan pengiriman perintah UDP.

### 6.4 Robot Controls (Batas Kecepatan)

a. Untuk setiap field perintah yang dilaporkan robot (misalnya kecepatan linear/angular), tersedia baris **minimum**, **maksimum**, dan **laju perubahan (ramp rate, unit/detik)**.
b. Batas ini hanya dapat **mempersempit**, tidak bisa melebihi batas bawaan dari firmware robot.
c. Mengatur batas maksimum mundur (yVelocity) ke `0` akan membuat mode mundur tidak mengirim apa pun meski tombol arah sudah dibalik.

### 6.5 Camera Rotation (PTZ)

| Kolom | Keterangan |
|---|---|
| PTZ camera IP *(opsional)* | Alamat IP kamera yang menerima perintah pan/tilt/zoom/fokus/lampu |

a. Kosongkan field ini untuk menonaktifkan seluruh fitur PTZ (tidak ada permintaan HTTP ke kamera yang dikirim backend).
b. Kredensial kamera PTZ **tidak** diatur di sini — sudah ditetapkan di sisi backend.
c. Kontrol PTZ hanya berfungsi saat nilai ini sama dengan alamat kamera yang sedang tampil di Home.

### 6.6 Recording (Lokasi Penyimpanan)

a. Pilih kartu tujuan rekaman dari daftar lokasi penyimpanan yang terdeteksi backend (`GET /storage/targets`).
b. Operator memilih **kartu/lokasi**, bukan path mentah — path sesungguhnya ditentukan backend.
c. Jika lokasi (mis. kartu SD) tidak terpasang, halaman akan menampilkan status "tidak tersedia" tanpa dianggap error.

### 6.7 Text Input (Keyboard Layar)

a. Toggle **Enabled/Disabled** untuk mengaktifkan/menonaktifkan keyboard layar bawaan.
b. Saat aktif, field teks di Settings bersifat *read-only* terhadap keyboard fisik dan dibuka lewat keyboard layar (dinavigasi dengan D-pad dan stick kiri).
c. Field sensitif (misalnya password) disamarkan saat diketik melalui keyboard layar.

> **Menyimpan Pengaturan**: setelah mengubah field apa pun, tekan tombol **Save** di bagian bawah panel. Fokus akan kembali ke tombol Save setelah tersimpan. Perubahan pada kamera, PTZ IP, atau transport akan membuat backend menyambungkan ulang hanya sumber yang terdampak — ditandai lewat notifikasi dari backend, bukan inisiatif aplikasi sendiri.

---

## Bagian 7: Perekaman dan Pemutaran Ulang

### 7.1 Memulai dan Menghentikan Rekaman

a. Tekan tombol **Record** di layar untuk memulai perekaman seluruh sumber kamera yang terkonfigurasi secara bersamaan.
b. Tekan kembali untuk menghentikan.
c. Status rekaman ditentukan oleh backend; aplikasi tidak menebak status saat tersambung ulang — status terbaru selalu diambil ulang dari backend.

### 7.2 Membuka Halaman Recordings

a. Tekan tombol **Recordings** di layar untuk membuka pustaka rekaman.
b. Daftar sesi rekaman ditampilkan; setiap sesi dapat dibuka untuk melihat daftar berkas di dalamnya.
c. Navigasi halaman ini memakai D-pad/A/B yang sama seperti Settings (lihat Bagian 8); Settings dan Recordings tidak pernah terbuka bersamaan.

### 7.3 Memutar, Menyimpan, dan Menghapus Rekaman

a. Tekan **A** pada sesi untuk membuka daftar berkasnya, lalu **A** pada berkas untuk memutar.
b. Selama pemutar video terbuka, D-pad sepenuhnya dikuasai pemutar (tidak bisa kembali ke daftar); tekan **B** untuk menutup pemutar sebelum menutup halaman.
c. Slider "start-from" pada pemutar mengatur titik mulai pemutaran (dalam dua puluh bagian dari durasi klip) — video diputar ulang dari backend mulai titik tersebut.
d. Sumber yang bukan berformat video standar (mis. kamera MJPEG) memerlukan konversi (*transcode*) sebelum diputar; aplikasi akan menawarkan opsi ini secara eksplisit saat diperlukan.
e. Sesi yang masih berjalan (`active`) tidak menampilkan opsi **Delete** sampai rekaman dihentikan.
f. Gunakan opsi **Save**/**Delete** pada berkas untuk menyimpan ke perangkat lain atau menghapus dari penyimpanan robot.

---

## Bagian 8: Navigasi Antarmuka dengan Gamepad

| Input | Pada Home | Pada Settings / Recordings / Keyboard Layar |
|---|---|---|
| D-pad | Pan/tilt kamera PTZ saja | Pindah fokus antar kontrol / antar tombol keyboard (kiri-kanan pada slider mengubah nilai) |
| A | Ganti mode maju/mundur | Aktifkan kontrol yang sedang fokus / tekan tombol keyboard |
| B | Ganti sumber kamera | Tutup panel / batalkan keyboard layar |
| X | Fokus kamera (tekan = dekat, tahan = jauh) | Tidak berfungsi |
| Y | Nyala/mati lampu inframerah | Tidak berfungsi (dan tidak berfungsi saat PTZ tidak aktif di kamera tampil) |
| Stick kiri | Kontrol gerak robot | Navigasi keyboard layar saja |
| Stick kanan | Kontrol belok robot | — |

Catatan: Settings/Recordings memiliki prioritas navigasi lebih tinggi dari Home, dan keduanya tidak pernah terbuka bersamaan. Input yang ditahan (misal menahan D-pad) akan mengulang setelah jeda awal, mengikuti perilaku pengulangan tombol standar.

---

## Bagian 9: Indikator Status dan Koneksi

- **Status koneksi backend** — ditampilkan di bilah atas; aplikasi otomatis mencoba menyambung ulang dan mengirim ulang konfigurasi serta status kontrol terakhir saat berhasil tersambung kembali.
- **Status kamera per sumber** — "menyambung", "tersambung", atau status error dengan tombol "Hubungkan lagi"; kamera yang diam tanpa frame video selama 5 detik dianggap gagal dan otomatis disambungkan ulang.
- **Indikator baterai** — pojok kanan atas, dapat ditekan untuk berganti antara baterai robot dan baterai Steam Deck. Jika robot tidak melaporkan level baterai pada paket telemetrinya, indikator menampilkan "tidak dilaporkan".
- **Lampu inframerah kamera** — status tombol lampu di layar selalu mencerminkan status sebenarnya di kamera (bukan status terakhir ditekan operator), karena backend menyiarkan ulang status saat tersambung dan setiap kali berubah.

---

## Bagian 10: Mengakhiri Sesi

a. Hentikan rekaman bila masih berjalan (lihat 7.1).
b. Tekan tombol **Exit** di layar untuk keluar dari aplikasi.
c. Pastikan robot dimatikan/diparkir dengan aman sesuai prosedur perangkat keras (lihat manual perangkat keras robot, di luar cakupan dokumen ini).

---

## Catatan untuk Finalisasi

- [ ] Tambahkan tangkapan layar (screenshot) tiap bagian — Home, Settings (tiap sub-panel), Recordings, pemutar video.
- [ ] Konfirmasi apakah manual ini perlu mencakup Bagian "Pemasangan/Instalasi Aplikasi" (build/instal APK/exe) atau cukup asumsi aplikasi sudah terpasang.
- [ ] Konfirmasi gaya bahasa: Indonesia formal seperti draf ini, atau campuran dengan istilah teknis Inggris dipertahankan tanpa terjemahan.
- [ ] Apakah perlu bagian Troubleshooting (mis. kamera gagal konek, UDP tidak terkirim, gamepad tidak terdeteksi)?
- [ ] Verifikasi nama tombol gamepad sesuai perangkat yang dipakai di lapangan (Xbox-style A/B/X/Y vs PlayStation-style seperti pada contoh manual robot pipa).
