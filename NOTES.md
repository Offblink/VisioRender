# VisioRender — 开发笔记

给**改这个包的人**看的。语义都是从真实 `.vsdx` + 真实 Visio 导出的 PNG 反向对账出来的，
每条都标了踩过的坑；README 只写使用者的事。

## 包怎么读

`.vsdx` = OPC/ZIP。读取顺序（`render()` 开头）：

1. `visio/pages/_rels/pages.xml.rels` → 找到 `page1.xml`（属性单双引号都收，目标可能带路径）
2. `visio/document.xml` 的 `<StyleSheet>` → 样式表（**可缺**，缺了所有值就地取自形状）
3. `visio/masters/masters.xml` + `masters.xml.rels` → 母版几何（**可缺**）
4. `visio/pages/page1.xml` → 形状；每个形状按 `实例 → 母版 → 样式表` 三级取值

## 反向出来的 Visio 语义

### 1. `EllipticalArcTo` 的 A、B 是**弧上的点**，不是切线交点

Visio 文档的措辞是 "the control point is best located about halfway between the beginning and
ending vertices"——即弧的中点。**按二次贝塞尔处理会把圆画成透镜**：⊗ 乘节点的圆盘、`Physical pull`
的回环箭头全都压扁。正确解法：起点 P0、终点 P1、中点 M 三点定圆（`arc_through_points`），
绕圆心按「扫过 M」的方向取样。退化（三点共线）时退回折线。

反例：第一版把 `Circle` 主母版的半圆画成高度 75% 的贝塞尔弧，圆盘在 16px 导出里明显是椭圆。

### 2. 1-D 形状的端点信 `BeginX/BeginY/EndX/EndY`

Visio 自己算好的四个数写在实例里。**不要**用 `LocPinY` 推：1-D 形状的 `Height` 和 `LocPinY`
经常从母版继承（母版的值和实例的 Width 不配套），结果整条线横偏 0.5 in —— 第一版所有竖箭头
连带中间列整体左偏 100 px，就是这么来的。实现（`have_be` 分支）：

- 只有两个几何点 → 直接用 Begin/End；
- 多段（动态连接线的折线路由）→ 用 Begin 反解出 LocPin，再套同一个变换。

验收口径：把所有 1-D 形状渲染出的端点和文件里的 `BeginX/EndX` 对账，误差应为 0（实测 47/47）。

### 3. 箭头：三级解析 + 必须同批绘制

- 解析链 `实例 cell → 母版 cell → document.xml 的 StyleSheet`。本文件里 `EndArrow=4` 写在
  样式 7/8/9（连接线/连接线箭头/点连接线）上，`BeginArrow=4` 写在实例上——只看实例会漏一半。
- **z 序**：箭头必须和它所属的线条**同一次循环里**画。单独开一遍最后统一画，箭头会盖到
  后画的形状上面——⊗ 的圆盘上就长出了箭头，Visio 里圆盘是压住箭头的。
- 箭头长度：`L = lineWidth × (1.6 + 0.5 × arrowSize)`，`arrowSize` 取 1~6（Visio 的档位）。
  这是拟合值，不是规范值；档位 6 的头宽度足以让测试断言"线本身够不到"。

### 4. 几何行按 IX 合并，按 cell 逐个回退

实例常常只写覆盖的那几个 cell（`<Row T='LineTo' IX='4'>` 只给 Y），X 要从母版的同一行取。
所以合并键是 `行 IX`，值是 `实例 cell or 母版 cell`（每个 cell 独立）。矩形、圆、梯形都靠这条。
`NoFill` / `NoLine` 是 Geometry **section 的直接 cell**（不是 Row），单独解析（`geom_flags`）。

### 5. 文字换行

- 可用宽度 = 形状宽 − 0.1 in × 2（Visio 文本块左右边距）。
- **行首行尾空格不计入换行宽度**，否则会提前换行（实测：`Train free Byte` 被拆到两行）。
  分词时把每个词做成「前导空格 + 词干」两段，行尾空格直接丢、行间空格算一次。
- 文字 run 的 `<cp IX='n'/>` 索引 ↔ `Character` section 的 `Row IX='n'`：字号/颜色/字体按 run 取，
  缺行回退到 IX=0。`Size` 单位是英寸（0.1389 = 10pt，0.3333 = 24pt）。
- 字体一律 Times New Roman 粗斜体渲染（PIL 度量与 Visio 差约 2%，在换行阈值上可忽略）。

### 6. 渐变：停靠里就是解好的颜色

`FillGradient` 的 `GradientStopColor` 带 `V` 字面 hex（虽然 `F` 是 `THEMEVAL(...)` 主题公式），
`GradientStopColorTrans` 是透明度。所以**不需要解主题色板**——需要的只是把它画出来：
按 `FillGradientAngle`（弧度，页坐标 y 向上）在图形 bbox 上做线性插值，alpha 与底层合成
（`paint_gradient`）。`FillGradientDir` 的非线性类型（矩形/射线）按同方向线性近似——已知近似。

### 7. 阴影：规则是拟合出来的

文件里两组互相矛盾的证据：16 个形状 `ShapeShdwOffsetX = -0.0417`，15 个是 `1E-9`（≈0），
而参照图上所有带 `ShdwPattern=1` 的框都是**右下 0.04 in**。按「右下 + |值| < 0.005 视为默认」
处理，参数用参照图拟合：

| 参数 | 取值 | 怎么定的 |
|---|---|---|
| 范围 | 只画显式 `ShdwPattern=1` 的形状（31 个） | 全画反而更差（色差 5.68 vs 4.65） |
| alpha | 0.16（扫描 0.12/0.18/0.24/0.30） | 最优点在 0.12，取 0.16 留住可见阴影 |
| 模糊 | 0.030 in | 扫描 0.018/0.025/0.032，几乎不敏感 |
| 方向 | 右下 | 参照图左右下三侧实测（右 199→253、下 194→250 的灰度梯度） |

三个参数都暴露成了 CLI 参数，换图重新拟合时不用改代码。

### 8. 容错

- `masters.xml` / `document.xml` 缺失 → 空表，不报错（夹具就是无母版包，测试覆盖这条）。
- 关系属性单引号、双引号都收。
- `FillPattern=0` / `LinePattern=0` / `NoFill=1` / `NoLine=1` → 不填 / 不描 / 裁掉。

## 验证账本

1. **重构不改行为**：脚本 → `render()` 函数的机械化改造后，对同一输入的输出 SHA-256 与改造前
   **字节一致**（改造中途丢过一次 `img.save` 和一次 `draw_arrows` 缩进，都是这条对账抓出来的）。
2. **与真实导出对齐**：以 SoftMax 框定标 + FFT 互相关细化 → `48.25 px/in`，页原点
   `(10.200, 13.864)`。之后所有差异都按这个坐标系量。
3. **指标演进**（模糊后平均色差 / ink XOR，参照图为一张真实 Visio PNG）：

   | 版本 | 色差 | XOR |
   |---|---|---|
   | 纯平色 | 12.06 / 255 | 7.5% |
   | + 渐变 | 4.77 / 255 | 5.8% |
   | + 阴影（最终） | **4.71 / 255** | **5.3%** |

4. **测试**（`tests/test_render.py` + `tests/test_association.py`，共 19 项）：包结构合法、尺寸与形状数、实心填充色、
   `shadow_mode=none` 必须是白的、双停渐变两端不同且都命中色板、圆的四个正交点必须有填充
   （针尖上是透镜就露馅）、箭头头部比线宽（线本身够不到的像素上必须是红的）、长文本必须换行
   而短文本必须是单行、CLI 正常路径与三个错误路径。
   夹具 `tests/fixtures/demo.vsdx` 由 `tests/make_fixture.py` 从零写出，**不含任何第三方母版**。

## 双击打开（`--register`）踩的两个解析坑

证据链（同一台机器、同一个 ProgID）：

- 全新扩展 `.vrtest` → 命令立刻跑通（`probe.png` 生成）；
- `.vsdx` → 仍然是 Edge 接手，**即使** `reg query HKCR\.vsdx /ve` 已经是 `VisioRender.vsdx`
  **且** `UserChoice` 不存在。

结论：Windows 解析扩展的实际顺序是

1. `FileExts\<ext>\UserChoice` —— 带哈希签名，**只能删不能写**；
2. `FileExts\<ext>\OpenWithProgids` —— 旧的 `VisioViewer.Viewer` 就排在这里，而它压过
   `HKCR\<ext>` 的默认值；
3. 才轮到 `HKCR\<ext>` 默认值（`reg query HKCR` 看到的是它，所以会给人"已经生效"的错觉）。

所以 `--register` 三件缺一不可：写 Classes 键、删 `UserChoice`、把 `OpenWithProgids`
里的别人挪开（挪了谁记进 `state.json`，`--unregister` 原样放回）。
对照实验：手工清掉 `OpenWithProgids\VisioViewer.Viewer` 后，`ouro.png` 立即生成 ✓。

`--unregister` 的还原边界：`UserChoice` 的哈希无法伪造，所以只能让它保持缺失 ——
后果无损（`HKLM\.vsdx` 默认仍是 `VisioViewer.Viewer`，即注册前的行为）。

## 嵌入图片：自动提取与 GDI+ 解码（两种模式）

改这条链时发现两个**一直存在、但从未被执行到**的 bug（旧代码把关系解析进 `_src` 就丢掉，
所以从没暴露）：

1. **`<Rel>` 藏在 `<ForeignData>` 里**，不是 `<Shape>` 的直接子元素 —— `sh.findall(V+'Rel')`
   恒为空（实测 ouro.vsdx：子元素只有 Cell×20、Section、ForeignData）。改成 `sh.iter(V+'Rel')`。
2. **关系路径丢了 `../`** —— `rsplit('_rels/',1)[0] + Target.replace('../','')` 得到
   `visio/pages/media/image1.emf`（不存在）；正解 `posixpath.normpath(prefix + target)`
   → `visio/media/image1.emf`。根绝对目标（`/visio/...`）lstrip 即可。

解码两路（`media.py`）：PNG/JPEG/GIF/BMP/TIFF → Pillow（RGBA 先合到白底）；
EMF/WMF → ctypes 调 `gdiplus.dll`：`GdipLoadImageFromFile`（字节落临时文件）→
画到 `GdipCreateBitmapFromScan0` 的 32bppARGB 位图（先 `GdipGraphicsClear` 不透明白）→
`GdipBitmapLockBits` 抠 BGRA 转 RGB。两个图都用 **`GdipDisposeImage`** 释放 ——
gdiplus.dll **不导出** `GdipDeleteBitmap`（实测 AttributeError）。

### ctypes 的坑：症状是静默白图，不抛异常

- **REAL 参数必须设 argtypes**：`GdipDrawImageRect` 的 x/y/w/h 是 float；不设 argtypes 时
  ctypes 把 Python int 按 C int 传，目的矩形变成 ~0 的垃圾浮点，GDI+ 返回
  `GenericError=2`，输出 0 个非白像素。设 `[c_void_p, c_void_p, c_float×4]` 后 status=0。
- `finally` 里句柄判空要判 `.value`：`c_void_p(0)` 恒为真，会对 NULL 调释放。

### 验证账本

- 同一张 EMF（ouro.vsdx 的 `image1.emf`，1801×1125）：本路径 vs PowerShell
  `System.Drawing` 参考 —— **逐像素 max diff 0，2,026,125 像素零差异**。
- 字节对账（同参渲染前后 SHA-256）：夹具 full、ouro full+`--media`（override 路径）、
  ouro vector 三条**不变**；ouro full 无 `--media` 从空白变贴图，且与手动
  `--media image1.png` 的输出**字节一致**。
- 契约：**两种模式**都画嵌入图片，并把它算进 vector 的裁剪外接框（vector 只裁剪内容，
  不再顺带丢内容 —— v0.1.1 之前 `foreign` 只在 full 分支收集，双击默认走的 vector 会把贴图
  整个跳过）；给了 `--media` → 一个 PNG 顶掉所有贴图；不给 → 每个 Foreign 各取各的关系图
  （多图页各贴各的）。解不出的部件 → 留白 + stderr
  `warning: cannot decode embedded picture: <part>`。
- v0.1.2 的字节对账：夹具 full/vector、ouro full（有/无 `--media`）四条 SHA-256 与改前一致；
  ouro vector 从 1609x1203 变 2040x4243（照片进外接框），且照片落点区域与原生解码图
  **逐像素 0 差异**。
- 测试 `tests/test_media.py`（7 项）：包由 `make_fixture.build()` 重封、页换成单个 Foreign；
  EMF 用例的 EMF 是测试用 gdi32 `CreateEnhMetaFileW` **现录的红矩形**，不夹第三方文件；
  另有两项钉 vector 语义（只含贴图时的画布=贴图+边距、图形与贴图并存时外接框取并集且两者都画）。

## launcher 为什么写成一个文件

- 注册的命令走 `pythonw.exe`（双击不闪黑框），**没有控制台** → 异常必须落盘，
  否则双击失败完全无感。launcher 把 traceback 追加到
  `%LOCALAPPDATA%\VisioRender\open.log`。
- launcher 里 `sys.path.insert(0, <包的父目录>)`：shell 启动时 cwd 不可控，
  这样不依赖安装方式（site-packages 或仓库目录）都能 import。
- `--open` 用 `os.startfile` 把 PNG 交给系统默认看图器，非 Windows 分支走
  `open` / `xdg-open`。

## 端到端验收（本机实测）

1. `--register` → `reg query` 四处键值与 `state.json` 均正确 ✓
2. `Start-Process -FilePath ouro.vsdx -Verb open`（与资源管理器双击同一条路径）→
   `ouro.png 生成: True`、`open.log` 不存在、默认看图器窗口标题 `ouro.png` ✓
3. `--unregister` → HKCU 两棵键删净、`OpenWithProgids` 恢复 `VisioViewer.Viewer`、
   `HKLM\.vsdx` 默认仍为 `VisioViewer.Viewer`（= 注册前行为）✓

## 图标

- `visiorender/assets/icon.svg`：蓝色圆角砖 + 白色放大镜 + 镜内 V。
  **V 是六点多边形而非文字**（`125,130 171,130 200,184 229,130 275,130 200,270`）——
  内顶点由 `y = H(1 − 2t/W)` 解出，所以 16px 下不依赖任何字体也读得出来。
- `icon.ico` 由该 SVG 经 Chromium `page.screenshot({omitBackground: true})` 光栅化成
  透明 PNG，再用 Pillow 写 7 档尺寸（16~256），供注册时的 `DefaultIcon` 使用。
- 为什么放在包里而不是仓库根：`DefaultIcon` 要指向安装后仍存在的文件，
  所以走 `[tool.setuptools.package-data]` 随包分发。

## 回归命令

```
python -m pytest -q          # 26 passed
ruff check .                 # All checks passed!
```

改了渲染几何之后，建议再跑一次字节对账（输入换成任意一个你手头真实文件，比较前后 SHA-256）。
