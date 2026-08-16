# 宠物形象素材来源

本目录下的 `*.png` / `*.webp` 由 `scripts/fetch_pets.py` 从
**Noto Animated Emoji**(Google)下载并规格化生成。

- 来源：https://googlefonts.github.io/noto-emoji-animation/
- 协议：**Creative Commons Attribution 4.0 International (CC BY 4.0)**
  https://creativecommons.org/licenses/by/4.0/
- 修改说明：按内容自动裁边、统一底部对齐、缩放至 256×256，动图重编码降低体积。

CC BY 4.0 **要求署名**。产品界面上的署名在教师控制台「学生与宠物」页
宠物墙下方（`apps/teacher/app.js` 的 `.credit`），另见 `apps/README.md`。
若本目录被单独复制走，请连同本文件一起带上。

前一版素材（Microsoft Fluent Emoji 3D，MIT 协议，无需署名）保留在
`apps/shared/pets-fluent3d/`。换回去需同时改两处物种清单：
`apps/shared/pet.js` 的 `SPECIES_ZH` 与 `apps/api/app/seed.py` 的 `SPECIES`。
