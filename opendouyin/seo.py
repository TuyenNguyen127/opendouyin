"""Bước 6a — SEO tiêu đề / mô tả / hashtag (thay cho TubeBuddy / VidIQ)."""
from pydantic import BaseModel, Field

from . import llm
from .utils import log, write_json


class SEO(BaseModel):
    titles: list[str] = Field(description="5 tiêu đề khác nhau, dưới 70 ký tự, gây tò mò")
    description: str = Field(description="mô tả 2-4 câu, có call-to-action")
    hashtags: list[str] = Field(description="8-12 hashtag, bắt đầu bằng #")
    keywords: list[str] = Field(description="10-15 từ khoá tìm kiếm")
    thumbnail_text: str = Field(description="chữ trên thumbnail, tối đa 6 từ, viết hoa, cực gây tò mò")
    series_idea: str = Field(description="gợi ý làm thành series nhiều phần nếu phù hợp")


SYSTEM = """Bạn là chuyên gia tăng trưởng kênh {platform} tại Việt Nam.
Tối ưu SEO cho video kể chuyện lồng tiếng {lang}: tiêu đề có từ khoá người Việt hay tìm,
khai thác tò mò / plot twist nhưng không giật tít sai sự thật. Hashtag mix giữa hashtag
rộng (#xuhuong, #fyp...) và hashtag ngách đúng chủ đề."""


def seo(script: dict, meta: dict, work, cfg) -> dict:
    story = "\n".join(l["text"] for l in script["lines"])
    prompt = (
        f"Tóm tắt: {script.get('summary', '')}\n"
        f"Hook: {script.get('hook', {}).get('text', '')}\n"
        f"Tiêu đề gốc (tiếng Trung): {meta.get('title', '')}\n\nScript:\n{story}"
    )
    system = SYSTEM.format(platform=cfg.seo.platform, lang=cfg.rewrite.target_language)
    data = llm.ask(cfg, "seo", work, system, prompt, SEO).model_dump()
    write_json(work / "seo.json", data)

    md = ["# Tiêu đề", *[f"{i}. {t}" for i, t in enumerate(data["titles"], 1)],
          "", "# Mô tả", data["description"], "", " ".join(data["hashtags"]),
          "", "# Từ khoá", ", ".join(data["keywords"]),
          "", "# Chữ thumbnail", data["thumbnail_text"], "", "# Ý tưởng series", data["series_idea"]]
    (work / "seo.md").write_text("\n".join(md), encoding="utf-8")
    log.info("SEO: %s", data["titles"][0] if data["titles"] else "")
    return data
