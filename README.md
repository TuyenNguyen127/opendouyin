# opendouyin: clone video hot Trung Quốc thành video tiếng Việt

Tool này tự động hoá quy trình **"xào lại video Trung đúng cách"** (theo bài của @ducbanpici).
Mỗi bước làm tay bằng một app trong bài được thay bằng một module code:

| # | Bước | Làm tay (theo bài) | Ở đây |
|---|------|--------------------|-------|
| 1 | Tìm "mỏ" content | Lướt Bilibili | `discover`: lấy Popular / Ranking / Search của Bilibili và chấm **điểm viral** (view × tương tác × độ mới) |
| 2 | Tìm & tải video | SnapAny | `download`: tải qua API Bilibili (DASH), yt-dlp cho Douyin/YouTube/TikTok, hoặc file local |
| 3 | Lấy sub & dịch | TurboScribe + ChatGPT/Gemini | `transcribe`: Whisper chạy local (GPU). `rewrite`: Claude dịch **và viết lại** script mới, kèm hook, gợi ý cắt đoạn thừa |
| 4 | Tạo giọng riêng | ElevenLabs / CapCut AI Voice | `tts`: edge-tts (miễn phí) hoặc ElevenLabs |
| 5 | Dựng lại video | CapCut | `edit`: ffmpeg cắt cảnh, đưa cao trào lên đầu, lật hình, zoom, chỉnh màu, khung 9:16 nền mờ, **khớp hình với giọng mới**, burn phụ đề, nhạc nền |
| 6 | Thumbnail + SEO | Canva + TubeBuddy/VidIQ | `seo`: Claude viết 5 tiêu đề, mô tả, hashtag, từ khoá, ý tưởng series. `thumbnail`: khung hình cao trào + chữ lớn |

## Cài đặt (Windows)

```bash
python -m venv .venv
```
```bash
.venv\Scripts\python -m pip install -r requirements.txt
```
```bash
copy .env.example .env
```

**Chọn AI viết script / SEO** (`llm.provider` trong `config.yaml`):

| provider | Chi phí | Cần gì |
|---|---|---|
| `ollama` (mặc định) | Miễn phí, chạy offline | Cài [Ollama](https://ollama.com), rồi `ollama pull qwen3:8b` (~5GB, vừa GPU 12GB) |
| `manual` | Miễn phí | Tool ghi prompt ra `projects/<id>/rewrite.prompt.txt`. Bạn dán vào ChatGPT, Gemini hoặc claude.ai, lưu câu trả lời vào `rewrite.answer.json`, rồi chạy `resume <id>` |
| `claude` | Khoảng $0.1–0.3/video | `ANTHROPIC_API_KEY` trong `.env`. Viết hay nhất, và là chế độ duy nhất đọc được hardsub ở video không có thoại |

- **ffmpeg**: nếu máy chưa cài thì tool dùng bản đi kèm `imageio-ffmpeg`. Nên cài bản đầy đủ: `winget install Gyan.FFmpeg`.
- **Whisper chạy GPU (NVIDIA)**: cài thêm thư viện CUDA bằng pip. Nếu không cài, tool tự chuyển sang CPU (chậm hơn khoảng 5–10 lần):
  ```bash
  .venv\Scripts\python -m pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12>=9,<10"
  ```
- **Bilibili 1080p**: tài khoản khách chỉ tải được 480p. Đăng nhập bilibili.com trên trình duyệt, copy cookie `SESSDATA` rồi dán vào `.env` dưới dạng `BILIBILI_SESSDATA=...`.

## Cách dùng

**1. Tìm video hot**
```bash
.venv\Scripts\python -m opendouyin discover
```
```bash
.venv\Scripts\python -m opendouyin discover --keyword "反转 剧情" --limit 10
```
Từ khoá tiếng Trung cho kết quả tốt nhất. Ví dụ: `反转` (plot twist), `短剧` (phim ngắn), `悬疑` (bí ẩn), `搞笑` (hài).

**2. Clone 1 video (chạy đủ 6 bước)**
```bash
.venv\Scripts\python -m opendouyin run https://www.bilibili.com/video/BV1xxxxxxxxx
```

**3. Sửa script trước khi lồng tiếng (nên làm)**
```bash
.venv\Scripts\python -m opendouyin run https://www.bilibili.com/video/BV1xxxxxxxxx --until rewrite
```
Mở `projects/BV1xxxxxxxxx/script.json` để sửa lời thoại, câu hook, các đoạn muốn cắt (`drop_ids`). Sau đó chạy tiếp:
```bash
.venv\Scripts\python -m opendouyin resume BV1xxxxxxxxx --from tts
```
TTS chỉ đọc lại những câu đã sửa (cache theo nội dung).

**4. Tự động cả lô: tìm hot rồi clone luôn top N**
```bash
.venv\Scripts\python -m opendouyin batch --keyword "反转" --top 3
```

## Kết quả trong `projects/<id>/`

| File | Nội dung |
|------|----------|
| `output.mp4` | Video hoàn chỉnh |
| `thumbnail.jpg` | Thumbnail |
| `seo.md` / `seo.json` | Tiêu đề, mô tả, hashtag, từ khoá |
| `script.json` | Script mới (sửa được) |
| `transcript.json` / `.srt` | Sub gốc tiếng Trung |
| `subs.srt` / `subs.ass` | Phụ đề mới, import được vào CapCut nếu muốn chỉnh tay |
| `timeline.json` | Từng block: lấy đoạn nào của video gốc, tốc độ giọng / hình |

## Cách khớp hình với giọng mới

Script do Claude viết gắn mỗi câu với các đoạn thoại gốc (`source_ids`), nên câu nói nào cũng đi với đúng cảnh của nó. Khi giọng mới dài hơn cảnh:
1. Tua giọng nhanh lên, tối đa `max_voice_speedup` (mặc định 1.25x).
2. Nếu vẫn dài hơn, cho hình chậm lại, tối đa `min_video_speed` (0.8x).
3. Còn dư nữa thì giữ khung cuối (freeze).

Đoạn không thoại giữ âm thanh gốc và bị cắt ngắn còn `max_gap` giây. Mỗi block được render đúng số frame và số mẫu audio rồi mới nối lại, nên video dài bao nhiêu tiếng và hình cũng không lệch nhau.

Video **không có thoại** (chỉ có nhạc + hardsub): tool trích khung hình và nhờ Claude đọc chữ trên hình / mô tả diễn biến, rồi viết script như bình thường.

## Tinh chỉnh (`config.yaml`)

Có thể sửa `config.yaml`, hoặc tạo file riêng rồi chạy với `-c my.yaml`:
- `edit.aspect`: `9:16` (TikTok/Shorts/Reels), `16:9` (YouTube), `source`
- `edit.blur_region: [0.80, 0.94]`: làm mờ dải chứa hardsub tiếng Trung (tính theo tỉ lệ chiều cao video gốc) và đặt sub tiếng Việt đè lên đúng dải đó
- `edit.mirror: false`: tắt lật hình cho video có chữ hoặc ảnh chụp màn hình app

Mỗi video có thể có config riêng. Ví dụ tạo `projects/<id>/config.yaml`:
```yaml
edit:
  mirror: false
  blur_region: [0.80, 0.94]
```
rồi chạy `python -m opendouyin -c projects/<id>/config.yaml resume <id> --from tts`.

**Mẹo chọn video:** tool cần video **có người nói** (thuyết minh, kể chuyện, kiến thức). Tìm bằng từ khoá như `冷知识` (kiến thức thú vị), `解说` (thuyết minh), `故事` (câu chuyện). Mỗi lần chỉ dùng **một từ khoá**, vì tìm nhiều từ cùng lúc dễ bị Bilibili chặn captcha. Video chỉ có nhạc, hoặc tác phẩm hoạ/hoạt hình gốc, thì không hợp.
- `edit.encoder: h264_nvenc`: render bằng GPU NVIDIA
- `edit.bgm`: thêm nhạc nền
- `tts.edge_voice`: `vi-VN-HoaiMyNeural` (nữ) / `vi-VN-NamMinhNeural` (nam)
- `rewrite.style`: giọng văn (hài hước, kể chuyện kinh dị, review phim...)
- `transcribe.model`: `large-v3` (chính xác nhất), `small` (nhanh, máy yếu)

## Dùng cùng Claude Code

Repo có sẵn các skill trong `.claude/skills/`. Mở thư mục này bằng Claude Code là skill tự được nạp:

| Skill | Dùng để |
|---|---|
| `opendouyin` | Chạy và chỉnh pipeline: tìm video hot, clone, sửa script, chạy lại từng bước |
| `agent-reach` | Đọc Bilibili / Xiaohongshu / YouTube (bình luận, phụ đề, bài hot) để tìm ý tưởng. Cần cài CLI [Agent Reach](https://github.com/Panniantong/Agent-Reach) |
| `video-use` | Chỉnh tay `output.mp4` sau pipeline: cắt, grade màu, overlay, animation. Bản sao từ [browser-use/video-use](https://github.com/browser-use/video-use) (MIT) |

## Lưu ý bản quyền

Video gốc là tài sản của tác giả. Chỉ dùng tool với nội dung bạn có quyền sử dụng, hoặc đã xin phép. TikTok, YouTube và Facebook đều có chính sách về "nội dung sử dụng lại" (reused content): video chỉ dịch và lồng tiếng lại có thể bị hạn chế phân phối hoặc không được bật kiếm tiền. Hãy coi video gốc là **tham khảo**: thêm góc nhìn, bình luận, cách kể riêng của bạn, và ghi nguồn khi cần.
