# Issue tracker: GitHub

Issues dan specs proyek ini menggunakan GitHub Issues di `sizoune/absensi-wajah-kantor`. Gunakan `gh` CLI dengan `--repo sizoune/absensi-wajah-kantor` secara eksplisit.

## Conventions

- Publikasikan spec sebagai GitHub issue. Gunakan `gh issue create --repo sizoune/absensi-wajah-kantor --title "..." --body-file <file>` untuk isi multiline.
- Baca issue menggunakan `gh issue view <number> --repo sizoune/absensi-wajah-kantor --json number,title,body,labels,comments`.
- Cari issue yang sudah ada sebelum membuat issue baru: `gh issue list --repo sizoune/absensi-wajah-kantor --state all --search "..." --json number,title,url`.
- Tambahkan atau hapus label dengan `gh issue edit <number> --repo sizoune/absensi-wajah-kantor --add-label "..."` atau `--remove-label "..."`.
- Komentar multiline memakai `gh issue comment <number> --repo sizoune/absensi-wajah-kantor --body-file <file>`.
- Tutup issue dengan `gh issue close <number> --repo sizoune/absensi-wajah-kantor` setelah pekerjaan benar-benar selesai.
- Ketika skill meminta mengambil ticket, baca GitHub issue terkait. Ketika skill meminta publikasi, buat GitHub issue.

## Pull requests as a triage surface

**PRs as a request surface: no.**
