# 振动监测站班次交接

工厂值班员从模拟振动记录器采集整数微米读数。记录器循环复用槽。班次固定结束后，值班员核齐应测原序并签发班单。下一班维护员可在停源时按成果号复领，检查极值、原包出处和纸面台账中的差异。

## 运行

使用 Python 3.11 标准库。平台物化工程时提供 `check_project.py`。

```sh
python3 -B check_project.py
python3 -B -m unittest discover -s tests -t . -v
python3 -B demo.py
```

演示在独立临时目录使用设备库和站库。设备依次录制 41、42、43 的 7、11、9 μm。补读 R42 已交出后才覆盖 42 的原槽。覆盖时班单待定，补包实收后得到三处实收、最小 7 μm、最大 11 μm。停源后核对纸面 42＝10 μm，巡检清单指出 42 的实读 11 μm。演示断言实际输出后打印业务场景 JSON。

同一演示入口还跑一个班内重启场景：A、B 两个启动同用槽 1，分别实收 A41＝7、A42＝11、B41＝9；A42 的补包在覆槽后才回到站库，签出后极值仍为 7 和 11 μm；停源后以纸值 7 分别核对 A41、B41，得到 `match` 与 `mismatch`。`check_project.py` 先核对 `restart_fixture.py` 的录制字节、槽复用和迟回补包，再运行全部回归，任一失败即非零退出。

## 值班流程与接口

1. 用 `station.open_station(path)` 初始化或打开站库，用 `station.device.DeviceRepository(path, device, slot_count)` 打开该设备的独立库。调用方关闭两个句柄。重复初始化保留已有登记、游标、槽、补读、实收、班单和复核。设备库绑定设备标识和槽数。
2. 用 `station.shift.open_shift(db, shift_id, device, start)` 登记班次。相同参数复领原登记，冲突登记报 `ValueError`。
3. 用 `station.device.parse_recording(text)` 读录制，再用 `station.device.ReplaySession(device_repo, events)` 和 `station.device.poll(session)` 推进事件。正常读值的 `result.image` 直接交给 `station.capture.receive(db, image)`。重复提交合法原包返回相同实收。站库原包以设备及原序为唯一身份。包体损坏、出处冲突和同位置异包报 `ValueError`。
4. 用 `station.shift.fix_bounds(db, shift_id, end)` 固定含首尾的班界。班外实收继续保存在站库。倒置和异参重投报 `ValueError`，未登记班次报 `KeyError`。
5. 用 `station.capture.request_repair(db, shift_id, device, sequence, operation, source=device_repo)` 先登记再向设备派发；省略 `source` 可将派发延到同参续办。尾序尚未固定时，可登记首序及以后的位置。固定尾序后，新登记必须在范围内。同参续办使用原登记。`station.device.query_repair(device_repo, operation)` 返回 `unknown`、`pending`、`not_issued` 或 `issued`。设备由录制 `issue` 事件或 `station.device.issue_repair` 实际取件。`issued` 的 `image` 按原号保留，随后的 `reply` 或按原号查询可恢复原包。用 `receive(db, image, operation=operation, source=device_repo)` 验收补包。
6. 用 `station.closeout.close_shift(db, shift_id, source=device_repo)` 签发。结果 `status` 为 `waiting` 或 `published`。待定时 `pending` 给出尚未定局的原序，`publication` 为空。缺位只有确证覆盖且该位置所有已登记补读均确证未交出才列作缺测。设备端覆盖后也无法为新的原号取得旧槽。覆盖期间可继续实收其他位置。已发布班单包含每个位置的终局、实收原包与原槽出处、统计及首执。无实测时极值为 JSON `null`。站库写入错误由 SQLite 报告，发布事务整体回滚。
7. 用 `station.review.retrieve(db, result_id)` 复领固定结果，成果号为 `shift/` 加班号。首执、班界、原包和统计均来自发布记录。未知成果报 `KeyError`。
8. 用 `station.review.reconcile(db, result_id, sequence, reported_um)` 记一次纸面核对。结论为 `match`、`mismatch` 或 `missing`。班外原序报 `ValueError`。`station.review.build_checklist(db, result_id)` 按源序返回缺测及有读值差异的位置；每个位置保留最近一条差异作为巡检依据，原复核记录仍可查询。

## 录制与原包

录制是 JSON 数组。`record` 事件含 `slot`、`sequence`、`payload`，其中 `payload` 为原包字节的十六进制表示。覆盖旧槽时返回对应旧原序。`cover` 含当前槽及其预期原序，显式移除该槽。`issue` 和 `reply` 含 `operation`。`issue` 只推进设备取件，`reply` 提供已交出的原件。录制结束时 `poll` 返回 `None`。格式错误或与当前设备状态冲突的事件报 `ValueError`，游标及槽保持已提交状态。续跑录制须保留已提交的事件前缀，可以追加后续事件。

班内重启见 `RESTART.md`：录制首段以 `{"kind":"boot","boot":"A"}` 标记，一次真正重启再以另一合法名 `{"kind":"boot","boot":"B"}` 标记；`record` 归属最近一次启动，`cover` 可带 `boot` 说明所覆盖的启动。`station.shift.open_shift(db, shift_id, device, (("A",41,42),("B",41,41)))` 登记并按启动顺序固定两段。新录制的 `poll(session).image` 带 `boot`，直送或补包都须经设备库核对实际录出的启动与原字节。实收、待定、缺测、班单位置及复核都以 `(启动名, 原序)` 定位；整数起点的旧班保持原样。

原包由 UTF-8 JSON 数组 `[device, sequence, slot, micrometers]`、换行和十六进制 SHA-256 校验和组成。`station.capture.packets.encode_packet` 可为录制生成原包。录制外层验证保留载荷，正式实收再核验原包。设备、班次与补读标识为非空 UTF-8 字符串，序号及槽号为非负 SQLite 整数，读值为有符号 SQLite 整数。日常读件按录制事件送站，错过日常投递时使用按原号补读。

固定区间按设备原序逐位计算。统计只使用正式实收，单位为 μm，原值无舍入，0 μm 是有效实测。每个固定缺测位置单列。有效重投、坏件、在途件和班外实收均不会增加本班正式实收数。重启班按各段启动先后再按段内原序计算；同序的两个读数分别作为独立实测。
