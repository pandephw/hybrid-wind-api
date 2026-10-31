WINDKITE HYBRID WIND API — STARTER v0.1
========================================
Tujuan: membuat sumber JSON Hybrid BMKG + Open-Meteo otomatis, tanpa server berbayar.
Status: PROTOTIPE EKSPERIMENTAL. Bukan prakiraan grid resmi BMKG dan bukan izin/kepastian keamanan layang-layang.

1. APA ITU API?
API adalah alamat internet yang memberikan data terstruktur, misalnya JSON, bukan halaman peta.
Aplikasi Wind–Kite HTML nantinya membaca JSON ini dan menggambar streamline sendiri.

2. HOSTING GRATIS UNTUK TAHAP PERTAMA: GITHUB ACTIONS + GITHUB PAGES
A) Buat akun di https://github.com dan login.
B) Buat repositori publik baru, namai 'hybrid-wind-api'.
C) Unggah semua isi folder paket ini termasuk folder tersembunyi .github/workflows.
   (Jika browser tidak bisa unggah folder tersembunyi, gunakan GitHub Desktop atau buat file .github/workflows/build-and-publish.yml lewat editor GitHub.)
D) Buka Settings > Pages > Build and deployment > Source > GitHub Actions.
E) Buka Actions > 'Update and publish Hybrid Wind API' > Run workflow.
F) Periksa hasil di Actions: semua langkah hijau berarti build+publikasi sukses.
G) Alamat JSON sesudah berhasil:
   https://USERNAME.github.io/hybrid-wind-api/v1/latest.json
   https://USERNAME.github.io/hybrid-wind-api/v1/times.json
   https://USERNAME.github.io/hybrid-wind-api/v1/metadata.json
   https://USERNAME.github.io/hybrid-wind-api/v1/grids/20261101T0400Z.json (contoh nama, hanya jika tersedia)
   Ganti USERNAME dengan username GitHub kamu. Jam/tanggal file grid harus dibaca dari times.json.
H) Scheduler menjalankan 4 kali per hari (UTC), tapi GitHub tidak menjamin tepat pada menitnya.
   Jika build API sumber gagal, workflow gagal dan halaman lama tetap tampil; cek generated_at_utc.
   Jadwal GitHub Actions repo publik bisa dinonaktifkan bila tak ada aktivitas repo selama 60 hari.

3. JALANKAN DI WINDOWS SEBELUM PUBLISH (OPSIONAL)
   cd C:\path\ke\hybrid_wind_api_starter
   python build_api.py
   python -m http.server 8000 --directory public
   buka http://localhost:8000/v1/latest.json
Python 3.11+; skrip tidak memerlukan instalasi pustaka tambahan.
Memerlukan internet untuk mengambil API sumber, dan sumber BMKG yang membalas dengan JSON valid.

4. BAGAIMANA PERHITUNGAN HYBRID?
- BMKG: 27 kode ADM4 *kandidat* dari dashboard v0.7; HARUS diverifikasi terhadap JSON BMKG nyata.
  Koordinat setiap wilayah diambil dari data BMKG, bukan titik buatan.
- Open-Meteo: model ncep_gfs_seamless untuk cell_selection=land dan cell_selection=sea.
- Waktu disamakan dalam UTC; BMKG 3-jaman dan GFS per jam.
- Arah meteorologi FROM dan kecepatan diubah menjadi U/V dalam m/s.
- Residual BMKG minus GFS di titik sumber diinterpolasi IDW, dengan pembatasan pengaruh dekat sumber.
- Pesisir: bobot transisi +/- 3 km berbasis garis pantai INDIKATIF versi dashboard v0.7.
- Hanya menghasilkan output jika minimum 6 dan >=60% titik sumber valid dan jam sinkron.
- Grid 41x41 adalah grid OUTPUT untuk visualisasi, BUKAN resolusi data asli.
- Jangan sebut prakiraan angin real-time; ini adalah prakiraan numerik terbaru menurut jadwal API.

5. API BERPARAMETER (TAHAP KEDUA, OPSIONAL): CLOUDFLARE WORKERS GRATIS
Setelah GitHub Pages menghasilkan JSON yang valid:
A) Daftar di https://dash.cloudflare.com
B) Masuk Workers & Pages > Create Worker (pilih JavaScript).
C) Ganti kode contoh dengan seluruh isi cloudflare/worker.js.
D) Ubah DATA_ROOT pada awal kode dari https://USER.github.io/hybrid-wind-api/v1/
   menjadi alamat GitHub Pages kamu yang sudah teruji.
E) Tekan Deploy; Cloudflare memberikan alamat HTTPS *.workers.dev
F) Uji (contoh, setelah situs milikmu benar-benar aktif):
   https://NAMA-API.SUBDOMAIN.workers.dev/v1/health
   https://NAMA-API.SUBDOMAIN.workers.dev/v1/times
   https://NAMA-API.SUBDOMAIN.workers.dev/v1/field
   https://NAMA-API.SUBDOMAIN.workers.dev/v1/point?lat=-8.75&lon=115.17
Parameter 'time' opsional; jika kosong memilih grid pertama (umumnya jam prakiraan terdekat).
Cloudflare hanya membacakan hasil JSON terjadwal; tidak mengulang 27 panggilan BMKG setiap pengunjung.
Paket ini TIDAK otomatis membuat akun, repo, domain, atau deployment.

6. STRUKTUR HASIL
public/v1/latest.json   -> status build, waktu pembuatan, error per ADM4, file terbaru
public/v1/times.json    -> daftar jam prakiraan dan nama berkas tiap jam
public/v1/grids/*.json  -> u_ms dan v_ms (south-north, west-east) + metadata QC
public/v1/metadata.json -> domain/resolusi visualisasi, sumber dan metode

7. PENTING SEBELUM PUBLIKASI KE MASYARAKAT
- Cantumkan dengan jelas: 'Data sumber: BMKG; model angin: Open-Meteo/GFS'.
- Open-Meteo data CC BY 4.0: atribusi + link, beri tahu bahwa data diolah.
- BMKG mewajibkan atribusi; cek persyaratan tambahan untuk redistribusi/API publik BMKG.
- Validasi independen dengan pengamatan AWS WADD. Jangan klaim 'aman bermain'.
- Verifikasi kode ADM4 dalam adm4_bali_selatan.json dan perbaiki yang salah.
- Ganti geo/coast_indikatif_v07.json dengan garis pantai resmi/berlisensi sebelum operasional.
- HTTPS untuk GPS, cek ketentuan privasi lokasi; API ini tidak perlu menyimpan GPS pengguna.
- Gagal pembaruan? Lihat log Actions dan generated_at_utc; jangan gunakan cache lama tanpa alarm.

Sumber dokumentasi:
https://data.bmkg.go.id/prakiraan-cuaca/
https://open-meteo.com/en/terms
https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages
https://developers.cloudflare.com/workers/get-started/dashboard/
