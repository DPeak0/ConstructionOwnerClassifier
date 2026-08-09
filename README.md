# 施工责任人图片分类器 1.1.2

Windows 10/11 x64 桌面工具。程序读取施工照片水印中的责任人字段，将图片复制或安全移动到对应责任人目录。设置和任务审计记录保存在本机 SQLite 数据库中。

## 使用流程

1. 从输入来源菜单添加一张或多张图片、文件夹，也可将图片和文件夹混合拖入输入框。
2. 选择结果保存位置并扫描；扫描完成后开始识别。
3. 高置信度结果自动分类；确认不存在施工水印的图片进入“无水印”目录；低置信度、未识别和异常记录可直接在右侧预览区复核。
4. 文件名搜索和责任人修正支持输入联想；确认后自动定位下一条待复核记录。
5. 在“设置”中可随时调整阈值、复制/移动策略、识别关键词和责任人名单，所有更改自动保存。

待复核预览支持滚轮按鼠标位置缩放、双击放大、左键拖动、工具栏缩放、适应窗口和原始大小。确认一条记录后会自动选择并显示下一条。责任人名单支持添加、修改、删除和 CSV 导入，别名会自动归并到真实姓名，例如“曹华斌”可归类为“曹华兵”。

已分类记录仍可选择新的责任人重新分类。程序会先写入并校验新目标文件，成功后再清理旧分类文件。并发识别数默认根据逻辑处理器和物理内存自动选择保守值，也可在识别设置中手动指定 1–4。

移动模式先在目标目录写入临时文件并校验大小和 SHA-256，确认完整后才删除原文件。未识别图片会进入“未识别”目录，无水印图片会进入“无水印”目录；移动失败时原文件保留。

## 开发运行

```powershell
python -m pip install -e ".[dev]"
python -m owner_classifier
```

应用数据目录为 `%LOCALAPPDATA%\ConstructionOwnerClassifier`。PP-OCRv6 Small 本地识别完全离线运行。AI 增强默认关闭；启用后优先发送 OCR 文字、坐标摘要和本地候选做语义辅助。本地 OCR 无法形成责任人候选时，才上传压缩后的疑似水印区域或整图做视觉兜底，AI 识别结果必须人工复核。API Key 使用 Windows 当前用户 DPAPI 加密保存。

## 软件更新

“设置 → 软件更新”可检查、下载并安装 `DPeak0/ConstructionOwnerClassifier` 的 GitHub Releases 正式版本。应用不依赖 GitHub API 配额，更新清单依次通过 jsDelivr、GitHub Raw、GitHub Release 和多个下载中继读取，以提高中国大陆无代理网络下的可用性。安装包下载完成后必须同时通过清单中的文件大小和 SHA-256 校验。

网络中继只负责传输，不决定版本或校验值。若所有线路均不可用，程序会保留当前版本并提示稍后重试，不会安装未校验文件。

## 开发测试构建

开发阶段默认生成便携测试目录，不制作安装包：

```powershell
.\scripts\build.ps1 -ReuseEnvironment
```

首次构建去掉 `-ReuseEnvironment`，脚本会创建独立 `.build-venv`。构建过程运行全部测试、生成精简 PyInstaller `onedir` 并执行打包后 OCR 烟雾测试，测试程序位于：

```text
dist\ConstructionOwnerClassifier\ConstructionOwnerClassifier.exe
```

只有正式发布工作流会向构建脚本传入 `-BuildInstaller`，再由 Inno Setup 生成当前用户安装包。安装包按当前用户安装到 `%LOCALAPPDATA%\Programs\ConstructionOwnerClassifier`，升级保留数据库和设置。

## 正式发布

正式发布工作流位于 `.github/workflows/release.yml`，唯一触发器是 GitHub `workflow_dispatch`。普通提交、合并和推送不会创建 Release。

确认版本号、测试和发布说明后，只有在明确要求“正式发布一次”时才运行：

```powershell
.\scripts\publish_release.ps1 -ConfirmFormalRelease -ReleaseNotes "本次正式发布说明"
```

工作流会重新运行全部测试、构建安装包、生成 SHA-256 更新清单、创建 `vX.Y.Z` GitHub Release，并更新供客户端检查的 `release-channel` 清单。本地构建不会触发发布。

## 测试

```powershell
pytest -q
pytest -q -m integration
```
