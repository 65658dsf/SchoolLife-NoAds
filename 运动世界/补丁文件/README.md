# 运动世界 7.3.80 广告补丁

本目录保存补丁源码和校验脚本，适用包名 `com.zjwh.android_wh_physicalfitness`、versionCode `402`。构建产物是本地签名的测试 APK；离线校验不能证明原生保护壳、登录、定位、跑步及服务器交互在手机上全部正常。

**当前状态：首版启动失败；启动修复测试包已生成，待手机复测（2026-09-13）。** 已定位保护壳内置证书摘要与重签名证书不一致，新增补丁仅更新该配置字段。首版 17 项检查与新增 26 项检查通过，不等于手机功能已验证。

## 修改范围

共修改 `embedded-08.dex` 内 5 个既有方法，保持方法容量、寄存器声明、索引、偏移、类结构及 native 声明不变。

| 目标方法 | 替换行为 | 保留的后续处理 |
| --- | --- | --- |
| `AdManager.isSlotAllowed(List,int,int)` | 返回 `false` | 原广告关闭分支、开屏跳转、悬浮容器释放及 SDK 轮播失败处理 |
| `AdManager.checkCanLoad(AdPosition,boolean)` | 返回 `false` | 原页面生命周期及业务代码 |
| `GuandianAdProvider.loadVideoAd(Activity,String,Function2)` | 清除加载标记并回调 `(false,false)` | 原广告不可用语义；不发放观看奖励 |
| `HomeFragment$setAdBanner$1$openAdDeferred$1.invokeSuspend(Object)` | 返回装箱的整数 `0` | 原协程清理 CMCC 广告条目、合并运营轮播的流程 |
| `SportFragment$setAdBanner$1$openAdDeferred$1.invokeSuspend(Object)` | 同上 | 同上 |

覆盖已定位、由上述入口控制的冷／热开屏、插屏、悬浮、SDK 轮播及跑步记录广告，以及首页和运动页的 CMCC 广告。激励视频不再加载，依赖观看广告的奖励入口会收到不可用结果。

运营活动、导航轮播与广告共用资源接口，不能整体删除。补丁保留 `/api/v70340/resource/slot/list`、主题配置、Flutter 公共网络桥和 SDK 初始化；未承诺过滤所有服务端新增内容或网页动态推广。

## 启动修复

`startup_fix.py` 只把加密配置的 `c` 字段从官方证书 MD5 更新为实际测试证书 MD5，保留其余 16 个字段、签名比较和拒绝逻辑。原生库无需修改。`build_startupfix.py` 复用首版签名密钥，导出实际证书、应用配置补丁，并验证最终 APK 证书与配置一致。

## 文件

| 文件 | 用途 |
| --- | --- |
| `ad_entry_specs.py` | 三处 SDK 入口的原始方法签名、元数据和处理依据 |
| `patch_ads.py` | 五处补丁定义、引用解析、版本校验和外层 DEX 校验和更新 |
| `inplace_dex.py` | 等长原位修改引擎，核对目标以外的字节没有变化 |
| `recover_payload.py` | 从指定原 APK 重建静态分析所需的 DEX 索引副本 |
| `test_patches.py` | 独立解码和模拟补丁指令，检查返回值、回调及修改范围 |
| `build_apk.py` | 首版构建与共享封装工具；直接执行只复现已知启动失败的首版 |
| `startup_fix.py` | 仅更新保护壳期望证书字段，保留校验代码 |
| `build_startupfix.py` | 当前修复版构建入口，复用签名密钥并核对配置和实际证书 |
| `test_startup_fix.py` | 26 项独立配置、证书、修改范围和分支语义检查 |
| `启动修复清单.json` | 本次追加补丁的原始／替换字节及哈希 |
| `启动修复校验结果.json` | 本次 26 项新增检查结果 |
| `原生签名分支校验.json` | 12 组恢复 ARM64 比较指令的隔离模拟结果 |
| `补丁清单.json` | 本次实际构建的偏移、原始／替换字节和哈希记录 |
| `离线校验结果.json` | 本次 17 项离线检查及指令模拟的详细结果 |

## 重建

需要 Python 3.11+、JDK 21，`java` 和 `keytool` 在 PATH 中。将原始 `ydsj.apk` 保持在上一级目录，在“运动世界”目录执行：

```powershell
python ".\补丁文件\build_startupfix.py"
```

默认输出 `output/运动世界-noads-startupfix-7.3.80.apk`。若同名 APK 已存在，脚本拒绝覆盖；可以指定另一个文件名：

```powershell
python ".\补丁文件\build_startupfix.py" --output "output/运动世界-noads-startupfix-7.3.80-rebuild.apk"
```

仅生成补丁副本并进行离线校验：

```powershell
python ".\补丁文件\build_startupfix.py" --prepare-only
```

签名工具优先读取 `../.analysis/tools/apksigner.jar`，当前环境也支持复用相邻参考项目的工具；可用 `--tools-dir` 指定包含该 JAR 的目录。脚本不自动下载或安装工具。工具 SHA-256 必须为：

```text
925fb5189d62fea563eaa24636108cb28a7281c05b63f3478f6c53e7768c7d3b
```

原 APK SHA-256 必须为：

```text
14e004e0001747ea49712a12d8657ad25eaf027de729b1e573d620a4bed345a1
```

新构建中间文件与日志保存在 `../.analysis/startup-fix/`，签名密钥继续保存在 `../.analysis/noads/`。继续更新本测试版时须保留 `local-signing.p12` 和 `local-signing.pass`，不要提交或公开这两个文件。脚本不安装应用、不卸载应用，也不更改原始 APK。

## 封装与验证边界

APK 保留原有保护壳和原生库。只替换外层 `classes.dex` 的五个目标指令区、加密配置 `c` 字段及标准头部校验和；内嵌 DEX 受保护的前 4096 字节保持原样。重建的可读 DEX 仅用于分析，不被打入 APK。

封装后逐个核对解压内容，仅 `classes.dex` 发生变化；Manifest、资源、assets、原生库和其他条目内容保持一致。ZIP 偏移、压缩结果及 APK 签名块会重新生成。未压缩 `.so` 数据按 16 KiB 对齐，其他未压缩条目按 4 字节对齐。

当前修复包与首版测试包证书一致，可覆盖首版测试包安装并保留数据；它与官方原版证书不同，不能普通覆盖更新官方原版。首版的正版提示退出已通过本地证书配置修复，修复包仍待手机实测。原生校验代码、其他环境检查和第三方签名鉴权保持原样，启动、登录、定位、跑步和记录同步仍需确认。
