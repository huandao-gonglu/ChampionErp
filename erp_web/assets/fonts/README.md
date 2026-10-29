# 水印字体

`NotoSansCJKsc-Regular.otf` 用于 Python/Pillow 的文字水印，随应用分发，确保中文、拉丁字母和西里尔字母不依赖部署机器的字体。

来源：[Noto CJK 官方仓库](https://github.com/notofonts/noto-cjk/tree/main/Sans/OTF/SimplifiedChinese)。原始字体文件未经修改；许可证见同目录 `OFL.txt`（SIL Open Font License 1.1）。更新字体时须重新验证水印渲染，并提升 `watermark_service.watermark_fingerprint` 中的渲染版本，避免复用旧字体生成的发布副本。
