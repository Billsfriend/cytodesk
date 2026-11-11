# CytoDesk 桌面打包

当前使用 **PyInstaller** 构建 Windows / macOS / Linux 桌面包。

PyInstaller 不支持跨平台交叉编译：

- Windows 包必须在 Windows 上构建
- macOS 包必须在 macOS 上构建
- Linux 包必须在 Linux 上构建
- 三个平台的产物不能互相替代

GitHub Actions 工作流会自动在目标系统上构建并上传产物：

```text
.github/workflows/build-desktop.yml
```

## 本地构建

在仓库根目录执行：

```bash
uv sync --group build
uv run pyinstaller packaging/cytodesk.spec --noconfirm --clean
```

产物位置：

```text
Windows: dist/CytoDesk/CytoDesk.exe
macOS:   dist/CytoDesk.app
Linux:   dist/CytoDesk/CytoDesk
```

## FlowKit 依赖

FlowKit 从 PyPI 安装，版本约束位于 `pyproject.toml`：

```toml
dependencies = ["flowkit>=1.3.2,<1.4"]
```

打包时会使用当前虚拟环境中安装的 FlowKit 版本，并将其代码一并打入
三个平台的产物。运行打包后的程序不再依赖本地 FlowKit 仓库。

CI 中同样通过 PyPI 安装，无需额外 checkout FlowKit。

## 发布工作流

工作流只在以下情况触发：

- 手动触发：Actions → **Build desktop packages with PyInstaller** → Run workflow
- 推送 `0.3.0` 或 `v0.3.0` 标签时自动构建并创建 GitHub Release：

```bash
git tag 0.3.0
git push origin 0.3.0
```

普通 `main` 分支提交不会自动触发构建，避免与标签构建重复。

构建矩阵：

- `windows-latest` → `CytoDesk-windows-x64.zip`
- `macos-latest` → `CytoDesk-macos-arm64.zip`
- `ubuntu-22.04` → `CytoDesk-linux-x64.tar.gz`

当前不构建 macOS Intel（x64）版本。

### Linux 包说明

Linux 包在 Ubuntu 22.04 上构建，目标为 x86_64：

- 最低依赖约为 glibc 2.35
- 建议在 Ubuntu 22.04 或更新版本上运行
- 解压后运行：

```bash
tar -xzf CytoDesk-linux-x64.tar.gz
./CytoDesk/CytoDesk
```

- 如果系统缺少 Qt/X11 运行库，安装：

```bash
sudo apt install libxcb-cursor0 libxkbcommon-x11-0 libgl1 libegl1
```

工作流已在 Ubuntu runner 上安装这些库，以便 PyInstaller 收集依赖。

## 签名与公证

当前工作流生成的是**未签名**包。

Windows：

- 用户首次运行可能出现 SmartScreen 警告
- 正式分发建议购买代码签名证书，并给 `CytoDesk.exe` 签名

macOS：

- 未签名 `.app` 在其它 Mac 上会被 Gatekeeper 拦截
- 用户可临时执行：

```bash
xattr -cr CytoDesk.app
```

- 正式分发建议配置 Apple Developer ID、`codesign` 和 `notarytool` 公证
- 可在工作流中增加签名 secrets；当前 spec 已预留 `codesign_identity` / `entitlements_file`

## 构建体积

PySide6、SciPy、Pandas 和 Bokeh 会使安装包较大。当前 Linux 测试包解压后约 430 MB。
如果后续需要减小体积，可以：

- 改用 PySide6-Essentials（需要额外处理 pyqtgraph 对 `PySide6.__version__` 的依赖）
- 继续排除未使用的 Qt 模块和插件
- 评估移除 FlowKit 的 Bokeh 依赖或延迟导入
