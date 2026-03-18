# 支持服务邮件模板：Worker 任务完成

会话执行完成/失败/取消时，Worker 会调用支持服务按模板给用户发邮件。模板 ID 按环境配置（见 `src/utils/constant.py`）：test 使用 `140`，uat 使用 `21`，prod 使用 `116`。

## 模板变量（params）

| 变量名 | 说明 |
|--------|------|
| `session_url` | 会话链接 |
| `user_question` | 用户问题（截断后） |
| `submitted_at` | 提交时间（如 2025-03-17 14:30:00 UTC） |
| `duration` | 运行时间（如 2 分 30 秒） |
| `result_status` | 结果：成功 / 失败 / 已取消 |
| `fail_reason` | 失败原因（仅失败时有内容，否则为 `-`） |
| `completed_at` | 完成时间（如 2025-03-17 14:35:00 UTC） |

## 注册/更新模板（template/add）

按环境选择对应域名执行 curl，请求体相同，仅 base URL 不同：

| 环境 | base URL |
|------|----------|
| test | `https://support.test.dp.tech` |
| uat  | `https://support.uat.dp.tech`  |
| prod | `https://support.dp.tech`     |

**test 环境：**

```bash
curl --location --request POST 'https://support.test.dp.tech/api/template/add?msg_name=Worker任务完成&business_line=Bohrium&channel=4' \
--header 'Content-Type: text/plain' \
--data-raw '<html>
<body>
<div id="editor_version_1.19.1_task_done" style="word-break:break-word;">
    <div data-zone-id="0" data-line-index="0" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;">尊敬的用户您好：
    </div>
    <div data-zone-id="0" data-line-index="1" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;">您在 MatMaster 上的会话已执行完毕，摘要如下：
    </div>
    <div data-zone-id="0" data-line-index="2" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;"><strong>用户问题：</strong>{{.user_question}}
    </div>
    <div data-zone-id="0" data-line-index="3" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;"><strong>提交时间：</strong>{{.submitted_at}}
    </div>
    <div data-zone-id="0" data-line-index="4" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;"><strong>完成时间：</strong>{{.completed_at}}
    </div>
    <div data-zone-id="0" data-line-index="5" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;"><strong>运行时长：</strong>{{.duration}}
    </div>
    <div data-zone-id="0" data-line-index="6" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;"><strong>执行结果：</strong>{{.result_status}}
    </div>
    <div data-zone-id="0" data-line-index="7" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;"><strong>失败原因：</strong>{{.fail_reason}}
    </div>
    <div data-zone-id="0" data-line-index="8" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;">可点击以下链接查看运行结果与输出文件：
    </div>
    <div data-zone-id="0" data-line-index="9" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;"><a href="{{.session_url}}">{{.session_url}}</a>
    </div>
    <div data-zone-id="0" data-line-index="10" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;">如有问题请联系：materials@dp.tech。
    </div>
    <div data-zone-id="0" data-line-index="11" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;">敬礼
    </div>
    <div data-zone-id="0" data-line-index="12" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6; text-align: right;">
        深势科技 MatMaster 产品组
    </div>
</div>
</body>
</html>'
```

**uat 环境：**

```bash
curl --location --request POST 'https://support.uat.dp.tech/api/template/add?msg_name=Worker任务完成&business_line=Bohrium&channel=4' \
--header 'Content-Type: text/plain' \
--data-raw '<html>
<body>
<div id="editor_version_1.19.1_task_done" style="word-break:break-word;">
    <div data-zone-id="0" data-line-index="0" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;">尊敬的用户您好：
    </div>
    <div data-zone-id="0" data-line-index="1" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;">您在 MatMaster 上的会话已执行完毕，摘要如下：
    </div>
    <div data-zone-id="0" data-line-index="2" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;"><strong>用户问题：</strong>{{.user_question}}
    </div>
    <div data-zone-id="0" data-line-index="3" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;"><strong>提交时间：</strong>{{.submitted_at}}
    </div>
    <div data-zone-id="0" data-line-index="4" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;"><strong>完成时间：</strong>{{.completed_at}}
    </div>
    <div data-zone-id="0" data-line-index="5" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;"><strong>运行时长：</strong>{{.duration}}
    </div>
    <div data-zone-id="0" data-line-index="6" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;"><strong>执行结果：</strong>{{.result_status}}
    </div>
    <div data-zone-id="0" data-line-index="7" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;"><strong>失败原因：</strong>{{.fail_reason}}
    </div>
    <div data-zone-id="0" data-line-index="8" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;">可点击以下链接查看运行结果与输出文件：
    </div>
    <div data-zone-id="0" data-line-index="9" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;"><a href="{{.session_url}}">{{.session_url}}</a>
    </div>
    <div data-zone-id="0" data-line-index="10" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;">如有问题请联系：materials@dp.tech。
    </div>
    <div data-zone-id="0" data-line-index="11" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;">敬礼
    </div>
    <div data-zone-id="0" data-line-index="12" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6; text-align: right;">
        深势科技 MatMaster 产品组
    </div>
</div>
</body>
</html>'
```

**prod（线上）环境：**

```bash
curl --location --request POST 'https://support.dp.tech/api/template/add?msg_name=Worker任务完成&business_line=Bohrium&channel=4' \
--header 'Content-Type: text/plain' \
--data-raw '<html>
<body>
<div id="editor_version_1.19.1_task_done" style="word-break:break-word;">
    <div data-zone-id="0" data-line-index="0" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;">尊敬的用户您好：
    </div>
    <div data-zone-id="0" data-line-index="1" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;">您在 MatMaster 上的会话已执行完毕，摘要如下：
    </div>
    <div data-zone-id="0" data-line-index="2" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;"><strong>用户问题：</strong>{{.user_question}}
    </div>
    <div data-zone-id="0" data-line-index="3" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;"><strong>提交时间：</strong>{{.submitted_at}}
    </div>
    <div data-zone-id="0" data-line-index="4" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;"><strong>完成时间：</strong>{{.completed_at}}
    </div>
    <div data-zone-id="0" data-line-index="5" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;"><strong>运行时长：</strong>{{.duration}}
    </div>
    <div data-zone-id="0" data-line-index="6" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;"><strong>执行结果：</strong>{{.result_status}}
    </div>
    <div data-zone-id="0" data-line-index="7" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;"><strong>失败原因：</strong>{{.fail_reason}}
    </div>
    <div data-zone-id="0" data-line-index="8" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;">可点击以下链接查看运行结果与输出文件：
    </div>
    <div data-zone-id="0" data-line-index="9" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;"><a href="{{.session_url}}">{{.session_url}}</a>
    </div>
    <div data-zone-id="0" data-line-index="10" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;">如有问题请联系：materials@dp.tech。
    </div>
    <div data-zone-id="0" data-line-index="11" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6;">敬礼
    </div>
    <div data-zone-id="0" data-line-index="12" data-line="true" style="margin-top: 4px; margin-bottom: 4px; line-height: 1.6; text-align: right;">
        深势科技 MatMaster 产品组
    </div>
</div>
</body>
</html>'
```

（成功/取消时 `fail_reason` 会传 `-`，邮件中会显示为「失败原因：-」。若模板引擎支持条件判断，可仅在 `fail_reason` 非 `-` 时渲染该行。）

发送侧使用 `businessLine: Bohrium`，邮件主题会根据结果自动为：
【MatMaster】您的会话已执行完成 / 您的会话执行失败 / 您的会话已取消。
