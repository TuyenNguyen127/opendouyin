---
name: opendouyin
description: Clone video hot Trung Quốc (Bilibili/Douyin) thành video tiếng Việt bằng pipeline opendouyin trong repo này — tìm video viral, tải, Whisper, viết lại script, TTS, dựng 9:16, SEO, thumbnail. Use when người dùng muốn tìm video hot Bilibili, "clone"/"xào lại"/lồng tiếng Việt một link Bilibili/Douyin/TikTok/YouTube, sửa script/giọng/phụ đề/SEO của một project trong projects/<id>/, hoặc chạy lại một bước của pipeline.
---

# opendouyin

Pipeline 7 bước, mỗi bước ghi 1 file vào `projects/<id>/`. Bước nào đã có file kết quả thì bị bỏ qua, trừ khi chạy lại bằng `--from`; khi một bước chạy lại thì mọi bước sau cũng chạy lại.

| Bước | File kết quả | Ghi chú |
|---|---|---|
| download | `source.mp4` | API Bilibili (DASH) hoặc yt-dlp |
| transcribe | `transcript.json` | faster-whisper local, `language: zh` |
| rewrite | `script.json` | LLM dịch + viết lại, có `hook`, `drop_ids`, `source_ids` |
| tts | `voices.json` | edge-tts / ElevenLabs, cache theo nội dung câu |
| edit | `output.mp4` | ffmpeg: khớp hình theo giọng, 9:16, burn sub |
| seo | `seo.json`, `seo.md` | tiêu đề, mô tả, hashtag |
| thumbnail | `thumbnail.jpg` | khung cao trào + chữ lớn |

Luôn chạy bằng Python trong venv của repo (Windows): `.venv\Scripts\python -m opendouyin ...`. Thêm `-v` để xem traceback khi lỗi.

## Lệnh

```bash
.venv\Scripts\python -m opendouyin discover --keyword "冷知识" --limit 10   # tìm video hot, chấm điểm viral
.venv\Scripts\python -m opendouyin run <link|file.mp4> --until rewrite      # clone, dừng lại để duyệt script
.venv\Scripts\python -m opendouyin resume <id> --from tts                    # chạy tiếp sau khi sửa script.json
.venv\Scripts\python -m opendouyin batch --keyword "反转" --top 3            # tìm + clone top N
.venv\Scripts\python -m opendouyin -c projects/<id>/config.yaml resume <id> --from edit   # config riêng cho 1 video
```

## Cách làm việc

1. **Chọn video**: cần video *có người nói* (thuyết minh, kể chuyện, kiến thức). Mỗi lần chỉ tìm **một** từ khoá tiếng Trung (`冷知识`, `解说`, `故事`, `反转`, `悬疑`, `搞笑`), vì tìm nhiều từ liên tiếp dễ bị Bilibili bắt captcha. Bỏ qua video chỉ có nhạc hoặc hoạt hình gốc.
2. **Mặc định dừng ở `--until rewrite`**, đọc `script.json` cùng người dùng, sửa hook / lời / `drop_ids`, rồi mới `resume --from tts`. TTS chỉ đọc lại câu đã đổi.
3. **Sửa hình**: đổi `edit.*` trong `projects/<id>/config.yaml` (ví dụ `mirror: false` cho video có chữ, `blur_region: [0.80, 0.94]` để che hardsub Trung), rồi `resume --from edit`.
4. **Sửa SEO / thumbnail**: `resume <id> --from seo`.
5. **Kiểm tra trước khi báo xong**: trích vài khung hình `output.mp4` bằng ffmpeg và xem; so độ dài video với `timeline.json`.

## LLM (`llm.provider` trong `config.yaml`)

- `ollama` (mặc định, offline): cần `ollama pull qwen3:8b`.
- `manual`: tool ghi `projects/<id>/rewrite.prompt.txt` rồi dừng. Bạn (Claude) có thể tự viết câu trả lời JSON đúng định dạng prompt yêu cầu vào `rewrite.answer.json`, sau đó `resume <id>`.
- `claude`: cần `ANTHROPIC_API_KEY` trong `.env`. Chỉ chế độ này đọc được hardsub ở video không thoại (vision fallback).

## Lỗi hay gặp

- Whisper báo thiếu cuBLAS/cuDNN: `pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12>=9,<10"`, hoặc đặt `transcribe.device: cpu`.
- Bilibili chỉ tải 480p: thêm `BILIBILI_SESSDATA=...` vào `.env` (người dùng tự lấy cookie, không xin mật khẩu).
- Captcha / 412 khi discover: đợi rồi thử lại với một từ khoá, đừng spam.

## Skill liên quan trong repo

- `agent-reach`: đọc bình luận, phụ đề, bài hot Bilibili / Xiaohongshu / YouTube để tìm ý tưởng hoặc kiểm tra xu hướng (chỉ đọc).
- `video-use`: chỉnh tay `output.mp4` sau pipeline (cắt thêm, grade màu, overlay, animation).

Nhắc người dùng về bản quyền: video gốc chỉ nên là tham khảo. Hãy thêm góc nhìn riêng và ghi nguồn.
