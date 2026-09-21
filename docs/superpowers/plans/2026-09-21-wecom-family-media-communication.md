# 企业微信家庭沟通与学生图片归档实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让授权老师通过企业微信机器人把图片安全归档到学生档案，并让普通微信家长通过微信客服绑定孩子、查询资料以及获得明确标注来源的机器人或老师回复。

**Architecture:** 新增独立的 `app.family` 领域，统一承载学生—老师—家长关系、邀请码、媒体和家庭会话；现有 `app.wecom` 仅作为老师渠道适配器，新增 `app.wecom_customer` 作为家长微信客服适配器。图片文件通过可替换的本机存储接口落在 `data/student_media`，数据库仅保存元数据；所有网页和渠道请求都调用同一套服务端权限判断。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy、SQLite、Jinja2、httpx、企业微信智能机器人 SDK、wechatpy、pytest。

---

## 文件结构

- `app/family/models.py`：关系、邀请码、图片、家庭会话与消息的 ORM 模型。
- `app/family/permissions.py`：老师和家长对学生的统一授权判断。
- `app/family/relationships.py`：老师关系、家长关系及班主任关系同步。
- `app/family/invitations.py`：一次性邀请码的生成、摘要、核销和撤销。
- `app/family/storage.py`：本机图片的临时写入、校验、正式落盘、隔离和读取。
- `app/family/media.py`：图片匹配学生、待确认任务和归档编排。
- `app/family/conversations.py`：家庭会话、消息、转人工和发送状态。
- `app/family/parent_assistant.py`：家长查询上下文、确定性图片查询和模型/人工路由。
- `app/family/routes.py`：学生关系管理、邀请码、图片读取和沟通记录网页路由。
- `app/wecom/media_handler.py`：老师机器人图片/图文消息适配。
- `app/wecom_customer/config.py`：微信客服配置。
- `app/wecom_customer/client.py`：access token、同步消息、发送文本/图片及临时素材上传。
- `app/wecom_customer/crypto.py`：回调验签和解密的薄封装。
- `app/wecom_customer/handler.py`：家长绑定、查询、提问和回复发送编排。
- `app/wecom_customer/routes.py`：微信客服 URL 验证和事件回调。
- `app/templates/profiles/detail.html`：学生档案图片区、关系和邀请码入口。
- `app/templates/family/manage.html`：管理员关系、邀请和沟通记录页面。
- `app/static/app.css`：图片网格和家庭沟通页面样式。
- `tests/family/*`、`tests/wecom/test_media.py`、`tests/wecom_customer/*`：领域、渠道和端到端测试。

### Task 1: 建立家庭关系与媒体数据模型

**Files:**
- Create: `app/family/__init__.py`
- Create: `app/family/models.py`
- Modify: `app/core/database.py`
- Modify: `db/schema.sql`
- Test: `tests/family/test_models.py`
- Test: `tests/test_database.py`

- [ ] **Step 1: 写模型注册与约束的失败测试**

```python
# tests/family/test_models.py
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.family.models import StudentImage, StudentTeacherAssignment


def test_family_tables_are_created(engine):
    names = set(inspect(engine).get_table_names())
    assert {
        "guardian", "student_guardian", "guardian_channel_binding",
        "guardian_invitation", "student_teacher_assignment", "student_image",
        "pending_media_assignment", "family_conversation", "family_message",
    } <= names


def test_inbound_image_position_is_idempotent(db_session, teacher, student):
    db_session.add(StudentTeacherAssignment(
        assignment_id="STA1", student_id=student.student_id,
        teacher_id=teacher.teacher_id, role="primary", start_date="2026-09-21",
        status="active",
    ))
    db_session.commit()
    kwargs = dict(
        student_id=student.student_id, uploaded_by_teacher_id=teacher.teacher_id,
        source_message_id="MSG1", source_position=0, caption="作业",
        mime_type="image/jpeg", extension="jpg", byte_size=3,
        sha256="a" * 64, storage_path="S1/2026/09/IMG1.jpg", status="active",
    )
    db_session.add(StudentImage(image_id="IMG1", **kwargs))
    db_session.commit()
    db_session.add(StudentImage(image_id="IMG2", **kwargs))
    with pytest.raises(IntegrityError):
        db_session.commit()
```

- [ ] **Step 2: 运行测试并确认因模块/表不存在而失败**

Run: `pytest -q tests/family/test_models.py tests/test_database.py`

Expected: FAIL，提示 `app.family.models` 不存在或新增表缺失。

- [ ] **Step 3: 新增九张 ORM 表和数据库注册**

在 `app/family/models.py` 中实现 `Guardian`、`StudentGuardian`、
`GuardianChannelBinding`、`GuardianInvitation`、`StudentTeacherAssignment`、
`StudentImage`、`PendingMediaAssignment`、`FamilyConversation` 和
`FamilyMessage`。字段逐项采用设计文档第 5 节所列名称；所有业务 ID 使用
`new_id()`，时间字段使用模块内统一的 `_utcnow()`。
`GuardianChannelBinding` 从首次建表起包含 `active_student_id` 和
`pending_state_json`，供绑定身份选择和多孩子选择使用，避免后续再追加列。

必须落实以下数据库约束：

- 状态和角色使用 `CheckConstraint`。
- `GuardianChannelBinding(channel, external_user_id)` 唯一。
- `StudentGuardian(student_id, guardian_id)` 唯一。
- `StudentImage(source_message_id, source_position)` 唯一。
- 图片、家庭会话和消息均使用业务 ID 文本主键。
- 所有时间保存 UTC ISO-8601 文本。

在 `app/core/database.py` 的 `_import_all_models()` 导入 `app.family.models`，把 `SCHEMA_VERSION` 从 7 增至 8。同步更新 `db/schema.sql`，使其忠实反映 ORM 表和索引。

- [ ] **Step 4: 运行模型和完整数据库测试**

Run: `pytest -q tests/family/test_models.py tests/test_database.py`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add app/family app/core/database.py db/schema.sql tests/family/test_models.py tests/test_database.py
git commit -m "feat: add family communication data model"
```

### Task 2: 实现学生—老师—家长关系与邀请码

**Files:**
- Create: `app/family/permissions.py`
- Create: `app/family/relationships.py`
- Create: `app/family/invitations.py`
- Test: `tests/family/test_relationships.py`
- Test: `tests/family/test_invitations.py`

- [ ] **Step 1: 写权限和邀请码失败测试**

```python
def test_head_teacher_is_synced_as_primary_assignment(db_session, classroom, student):
    enroll_student(db_session, student.student_id, classroom.class_id, date(2026, 9, 21))
    sync_head_teacher_assignments(db_session)
    assert teacher_can_access_student(db_session, classroom.head_teacher_id, student.student_id)


def test_invitation_is_stored_as_digest_and_can_only_be_redeemed_once(
    db_session, teacher, student
):
    invitation, plain_code = create_guardian_invitation(
        db_session, student.student_id, teacher.teacher_id,
        secret_key="test-secret", now=datetime(2026, 9, 21, tzinfo=timezone.utc),
    )
    assert invitation.code_hash != plain_code
    guardian = redeem_guardian_invitation(
        db_session, plain_code, external_user_id="WX-PARENT",
        relationship="father", guardian_name="张爸爸",
        secret_key="test-secret", now=datetime(2026, 9, 21, tzinfo=timezone.utc),
    )
    assert guardian_can_access_student(db_session, guardian.guardian_id, student.student_id)
    with pytest.raises(ValueError, match="邀请码已使用"):
        redeem_guardian_invitation(
            db_session, plain_code, external_user_id="WX-OTHER",
            relationship="mother", guardian_name="张妈妈",
            secret_key="test-secret", now=datetime(2026, 9, 21, tzinfo=timezone.utc),
        )
```

- [ ] **Step 2: 运行测试并确认缺少服务函数**

Run: `pytest -q tests/family/test_relationships.py tests/family/test_invitations.py`

Expected: FAIL，提示导入的服务函数不存在。

- [ ] **Step 3: 实现统一权限与关系同步**

公开接口固定为：

```python
def teacher_can_access_student(db: Session, teacher_id: str, student_id: str, on: date | None = None) -> bool
def guardian_can_access_student(db: Session, guardian_id: str, student_id: str) -> bool
def list_students_for_guardian(db: Session, guardian_id: str) -> list[Student]
def sync_head_teacher_assignments(db: Session) -> int
def assign_teacher(db: Session, student_id: str, teacher_id: str, role: str, start_date: date) -> StudentTeacherAssignment
def revoke_teacher_assignment(db: Session, assignment_id: str, end_date: date) -> StudentTeacherAssignment
```

权限查询只认可 `active` 且日期有效的关系。同步函数为每条当前有效入班关系创建缺失的主负责老师关系，不删除人工创建的任课或协同关系。

- [ ] **Step 4: 实现 HMAC 摘要的一次性邀请码**

公开接口固定为：

```python
def create_guardian_invitation(db, student_id, created_by_teacher_id, *, secret_key, now=None, ttl=timedelta(days=7)) -> tuple[GuardianInvitation, str]
def redeem_guardian_invitation(db, code, external_user_id, relationship, guardian_name, *, secret_key, now=None) -> Guardian
def revoke_guardian_invitation(db, invitation_id, revoked_by_teacher_id) -> GuardianInvitation
```

邀请码使用 `secrets` 生成易输入的 8 位大写字母数字组合；数据库保存 `HMAC-SHA256(secret_key, code)`。核销过程在同一事务内检查摘要、有效期、次数、撤销状态与学生状态，再创建渠道绑定和学生家长关系。

- [ ] **Step 5: 运行关系测试**

Run: `pytest -q tests/family/test_relationships.py tests/family/test_invitations.py`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add app/family/permissions.py app/family/relationships.py app/family/invitations.py tests/family
git commit -m "feat: add student family relationships and invitations"
```

### Task 3: 实现安全的本机图片存储

**Files:**
- Create: `app/family/storage.py`
- Test: `tests/family/test_storage.py`

- [ ] **Step 1: 写格式、限额、原子写入和隔离失败测试**

```python
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 32


def test_store_validates_content_and_returns_relative_path(tmp_path):
    store = LocalImageStore(tmp_path / "student_media", max_bytes=1024)
    saved = store.save("S1", "IMG1", [PNG])
    assert saved.mime_type == "image/png"
    assert saved.relative_path == "S1/2026/09/IMG1.png"
    assert (tmp_path / "student_media" / saved.relative_path).read_bytes() == PNG


def test_store_rejects_spoofed_or_oversized_content(tmp_path):
    store = LocalImageStore(tmp_path / "student_media", max_bytes=16)
    with pytest.raises(UnsupportedImage):
        store.save("S1", "IMG1", [b"not-an-image"])
    with pytest.raises(ImageTooLarge):
        store.save("S1", "IMG2", [PNG])
```

- [ ] **Step 2: 运行测试并确认存储类不存在**

Run: `pytest -q tests/family/test_storage.py`

Expected: FAIL。

- [ ] **Step 3: 实现流式写入和真实文件头识别**

实现：

```python
@dataclass(frozen=True)
class StoredImage:
    relative_path: str
    mime_type: str
    extension: str
    byte_size: int
    sha256: str

class LocalImageStore:
    def save(self, student_id: str, image_id: str, chunks: Iterable[bytes], now=None) -> StoredImage
    def open(self, relative_path: str) -> BinaryIO
    def quarantine(self, relative_path: str) -> str
    def remove_temporary(self, temporary_path: str) -> None
```

仅识别 JPEG、PNG、WebP 文件头；累计字节超过限额立即停止；先写同一磁盘下的 `.pending` 文件，再使用 `Path.replace()` 原子移动。所有解析后的目标路径必须经 `resolve()` 验证仍位于存储根目录内。

- [ ] **Step 4: 运行存储测试**

Run: `pytest -q tests/family/test_storage.py`

Expected: PASS，测试临时目录中不残留 `.pending` 文件。

- [ ] **Step 5: 提交**

```bash
git add app/family/storage.py tests/family/test_storage.py
git commit -m "feat: add secure local student image storage"
```

### Task 4: 实现图片归档与待确认业务流程

**Files:**
- Create: `app/family/media.py`
- Test: `tests/family/test_media.py`

- [ ] **Step 1: 写学生匹配、幂等和待确认失败测试**

```python
def test_archive_prefers_exact_student_id(db_session, authorized_teacher_scope, image_store):
    result = archive_teacher_images(
        db_session, image_store, teacher_id="T1", source_message_id="M1",
        caption="S2 王芳 作业订正", payloads=[PNG], now=NOW,
    )
    assert result.status == "archived"
    assert result.student_id == "S2"


def test_missing_or_duplicate_name_creates_pending_assignment(
    db_session, authorized_teacher_scope, image_store
):
    result = archive_teacher_images(
        db_session, image_store, teacher_id="T1", source_message_id="M2",
        caption="今天作业", payloads=[PNG], now=NOW,
    )
    assert result.status == "needs_student"
    assert {choice.student_id for choice in result.choices} == {"S1", "S2"}


def test_replayed_message_returns_existing_images(db_session, authorized_teacher_scope, image_store):
    first = archive_teacher_images(
        db_session, image_store, teacher_id="T1", source_message_id="M3",
        caption="S1 作业", payloads=[PNG], now=NOW,
    )
    second = archive_teacher_images(
        db_session, image_store, teacher_id="T1", source_message_id="M3",
        caption="S1 作业", payloads=[PNG], now=NOW,
    )
    assert second.image_ids == first.image_ids
```

- [ ] **Step 2: 运行测试并确认业务函数不存在**

Run: `pytest -q tests/family/test_media.py`

Expected: FAIL。

- [ ] **Step 3: 实现归档编排**

公开接口：

```python
@dataclass(frozen=True)
class MediaArchiveResult:
    status: Literal["archived", "needs_student", "duplicate", "rejected"]
    student_id: str | None
    image_ids: Sequence[str]
    choices: Sequence[StudentChoice]
    message: str

def archive_teacher_images(db, store, *, teacher_id, source_message_id, caption, payloads, now=None) -> MediaArchiveResult
def confirm_pending_media(db, store, *, teacher_id, pending_id, student_id, now=None) -> MediaArchiveResult
def expire_pending_media(db, store, *, now=None) -> int
def list_student_images(db, student_id, *, include_deleted=False, limit=50, offset=0) -> list[StudentImage]
def soft_delete_image(db, store, image_id, deleted_by_teacher_id) -> StudentImage
def restore_image(db, store, image_id, restored_by_teacher_id) -> StudentImage
```

匹配顺序固定为：完整业务 ID → 老师授权范围内的唯一姓名 → 待确认。确认时再次调用 `teacher_can_access_student`。数据库提交失败时删除刚落盘文件；文件保存失败时不添加数据库记录。

- [ ] **Step 4: 运行媒体领域测试**

Run: `pytest -q tests/family/test_media.py`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add app/family/media.py tests/family/test_media.py
git commit -m "feat: archive teacher images by student"
```

### Task 5: 接入老师企业微信图片和图文消息

**Files:**
- Create: `app/wecom/media_handler.py`
- Modify: `app/wecom/gateway.py`
- Modify: `app/wecom/config.py`
- Test: `tests/wecom/test_media.py`
- Test: `tests/wecom/test_runtime.py`

- [ ] **Step 1: 写图片事件、混合事件和候选按钮失败测试**

```python
@pytest.mark.asyncio
async def test_mixed_message_archives_image_and_replies_with_student(FakeClient, gateway):
    frame = mixed_frame(msgid="M1", user="WX1", text="S1 作业订正", image=ENCRYPTED_IMAGE)
    await gateway.client.handlers["message.mixed"](frame)
    assert gateway.client.stream_replies[-1].endswith("已归档到测试学生（S1），共 1 张。")


@pytest.mark.asyncio
async def test_image_without_student_sends_choice_card(FakeClient, gateway):
    await gateway.client.handlers["message.image"](image_frame(msgid="M2", user="WX1"))
    card = gateway.client.cards[-1]
    assert card["main_title"]["title"] == "请选择图片所属学生"
```

- [ ] **Step 2: 运行测试并确认当前“仅支持文本”回复导致失败**

Run: `pytest -q tests/wecom/test_media.py tests/wecom/test_runtime.py`

Expected: FAIL，图片和 mixed 事件仍走 `_unsupported`。

- [ ] **Step 3: 实现渠道解析和媒体下载注入**

在 `media_handler.py` 定义：

```python
def parse_media_message(body: dict) -> ParsedMediaMessage
async def download_and_decrypt_images(client, parsed: ParsedMediaMessage) -> list[bytes]
def process_teacher_media(db, store, *, binding_secret, wecom_user_id, parsed, payloads) -> MediaBotReply
def process_media_choice(db, store, *, wecom_user_id, event_key) -> MediaBotReply
```

`parse_media_message` 支持 `image` 和 `mixed`，从 mixed 项中合并文本并保持图片顺序；下载器使用 SDK 暴露的媒体下载/解密能力，任何异常只记录异常类型，不记录 URL、AES key 或消息内容。

- [ ] **Step 4: 修改网关注册与交互卡片**

`WecomGateway.__init__` 新增 `image_store`。分别注册 `message.image` 和 `message.mixed`；`voice/file/video` 继续明确拒绝。媒体候选按钮使用 `media_<pending_id>_<student_id>`，点击事件与现有 `scope_` 分支隔离。

在 `WecomConfig` 增加：

```python
media_root: Path = Path("data/student_media")
media_max_bytes: int = 10 * 1024 * 1024
pending_media_minutes: int = 15
```

并读取 `STUDENT_MEDIA_ROOT`、`STUDENT_MEDIA_MAX_BYTES` 和 `PENDING_MEDIA_MINUTES`。

- [ ] **Step 5: 运行企业微信媒体与回归测试**

Run: `pytest -q tests/wecom/test_media.py tests/wecom/test_runtime.py tests/wecom/test_handler.py`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add app/wecom tests/wecom
git commit -m "feat: accept teacher images from WeCom"
```

### Task 6: 在网页管理关系、邀请码和学生图片

**Files:**
- Create: `app/family/routes.py`
- Create: `app/templates/family/manage.html`
- Modify: `app/profiles/routes.py`
- Modify: `app/profiles/service.py`
- Modify: `app/templates/profiles/detail.html`
- Modify: `app/static/app.css`
- Modify: `app/main.py`
- Test: `tests/family/test_routes.py`
- Test: `tests/profiles/test_routes.py`

- [ ] **Step 1: 写网页鉴权与图片读取失败测试**

```python
def test_teacher_can_view_owned_student_image(teacher_client, seeded_owned_image):
    response = teacher_client.get("/family/images/IMG-OWNED")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_teacher_cannot_view_foreign_student_image(teacher_client, seeded_foreign_image):
    assert teacher_client.get("/family/images/IMG-FOREIGN").status_code == 403


def test_admin_can_create_invitation_and_plain_code_is_shown_once(client, student):
    response = client.post(f"/family/students/{student.student_id}/invitations", follow_redirects=False)
    assert response.status_code == 303
    page = client.get(response.headers["location"])
    assert "请立即复制" in page.text
```

- [ ] **Step 2: 运行路由测试并确认 404**

Run: `pytest -q tests/family/test_routes.py tests/profiles/test_routes.py`

Expected: FAIL，新增路由不存在。

- [ ] **Step 3: 实现受控图片响应和管理员动作**

新增路由：

```text
GET  /family/students/{student_id}
POST /family/students/{student_id}/teachers
POST /family/teacher-assignments/{assignment_id}/revoke
POST /family/students/{student_id}/invitations
POST /family/invitations/{invitation_id}/revoke
GET  /family/images/{image_id}
POST /family/images/{image_id}/delete
POST /family/images/{image_id}/restore
GET  /family/conversations/{conversation_id}
```

管理动作依赖 `require_admin`。图片 GET 依赖 `require_login` 并调用 `teacher_can_access_student`，管理员例外；响应使用 `StreamingResponse`，设置 `X-Content-Type-Options: nosniff`、私有缓存和安全的下载文件名。

- [ ] **Step 4: 扩展学生档案上下文与模板**

`StudentProfile` 增加 `images`、`guardians` 和 `teacher_assignments`。普通老师只看到图片列表；管理员额外看到关系、邀请码和会话管理入口。图片使用受控 URL `/family/images/{image_id}`，每页默认 24 张。

- [ ] **Step 5: 运行网页测试**

Run: `pytest -q tests/family/test_routes.py tests/profiles/test_routes.py tests/test_pages.py`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add app/family/routes.py app/templates/family app/profiles app/templates/profiles/detail.html app/static/app.css app/main.py tests/family/test_routes.py tests/profiles/test_routes.py
git commit -m "feat: manage family access and student images"
```

### Task 7: 建立微信客服配置、回调验签和 API 客户端

**Files:**
- Modify: `pyproject.toml`
- Create: `app/wecom_customer/__init__.py`
- Create: `app/wecom_customer/config.py`
- Create: `app/wecom_customer/crypto.py`
- Create: `app/wecom_customer/client.py`
- Create: `app/wecom_customer/routes.py`
- Test: `tests/wecom_customer/test_config.py`
- Test: `tests/wecom_customer/test_crypto.py`
- Test: `tests/wecom_customer/test_client.py`
- Test: `tests/wecom_customer/test_routes.py`

- [ ] **Step 1: 写配置、验签和客户端失败测试**

```python
def test_enabled_customer_channel_requires_all_credentials():
    with pytest.raises(ValueError, match="WECOM_CUSTOMER_CORP_ID"):
        load_customer_config({"WECOM_CUSTOMER_ENABLED": "true"})


def test_callback_verification_uses_decrypted_echo(fake_crypto, client):
    response = client.get(
        "/wecom/customer/callback?msg_signature=sig&timestamp=1&nonce=n&echostr=cipher"
    )
    assert response.text == "verified-echo"


def test_send_text_refreshes_token_once_on_expiry(httpx_mock):
    api = WecomCustomerClient(CONFIG, transport=httpx_mock.transport)
    api.send_text("EXT1", "【机器人回复】你好")
    assert httpx_mock.token_requests == 2
    assert httpx_mock.sent_messages == 1
```

- [ ] **Step 2: 运行测试并确认模块不存在**

Run: `pytest -q tests/wecom_customer/test_config.py tests/wecom_customer/test_crypto.py tests/wecom_customer/test_client.py tests/wecom_customer/test_routes.py`

Expected: FAIL。

- [ ] **Step 3: 添加回调加密依赖并实现配置**

在 `pyproject.toml` 添加 `wechatpy`。配置字段固定为：

```python
@dataclass(frozen=True)
class WecomCustomerConfig:
    enabled: bool
    corp_id: str | None
    corp_secret: str | None
    open_kfid: str | None
    callback_token: str | None
    callback_aes_key: str | None
```

对应环境变量为 `WECOM_CUSTOMER_ENABLED`、`WECOM_CUSTOMER_CORP_ID`、`WECOM_CUSTOMER_SECRET`、`WECOM_CUSTOMER_OPEN_KFID`、`WECOM_CUSTOMER_CALLBACK_TOKEN` 和 `WECOM_CUSTOMER_CALLBACK_AES_KEY`。

- [ ] **Step 4: 封装回调加密与企业微信客服 API**

`WecomCallbackCrypto` 使用 `wechatpy.crypto.WeChatCrypto`，公开 `verify_url()` 和 `decrypt_message()`。客户端公开：

```python
def sync_messages(self, cursor: str | None, token: str) -> SyncResult
def send_text(self, external_user_id: str, content: str) -> str
def upload_image(self, content: bytes, filename: str) -> str
def send_image(self, external_user_id: str, media_id: str) -> str
```

客户端访问企业微信 `gettoken`、`kf/sync_msg`、`kf/send_msg` 和临时素材上传接口；token 在过期前 60 秒刷新。日志不得包含 secret、access token、AES key、消息正文或媒体 URL。

- [ ] **Step 5: 实现 GET 验证和 POST 事件回调**

GET 返回解密后的 `echostr`。POST 验签解密后只提取同步 token，把 token 交给注入的处理器，再立即返回 `success`。相同事件 token 的重复回调必须可安全重放。

- [ ] **Step 6: 运行微信客服基础测试**

Run: `pytest -q tests/wecom_customer`

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add pyproject.toml app/wecom_customer tests/wecom_customer
git commit -m "feat: add WeCom customer service adapter"
```

### Task 8: 实现家长邀请码绑定与受限查询

**Files:**
- Create: `app/wecom_customer/handler.py`
- Create: `app/family/parent_assistant.py`
- Modify: `app/wecom_customer/routes.py`
- Test: `tests/wecom_customer/test_binding.py`
- Test: `tests/wecom_customer/test_queries.py`

- [ ] **Step 1: 写邀请码绑定、多孩子选择和越权失败测试**

```python
def test_parent_redeems_invitation_then_selects_relationship(db_session, invitation):
    replies = process_parent_text(
        db_session, fake_customer, secret_key="secret",
        external_user_id="EXT1", message_id="P1", text=invitation.code,
    )
    assert replies[-1].content == "请选择身份：父亲、母亲或其他监护人。"
    replies = process_parent_text(
        db_session, fake_customer, secret_key="secret",
        external_user_id="EXT1", message_id="P2", text="父亲",
    )
    assert replies[-1].content.startswith("【机器人回复】绑定成功")


def test_parent_cannot_request_unbound_student(db_session, bound_parent):
    replies = process_parent_text(
        db_session, fake_customer, secret_key="secret",
        external_user_id="EXT1", message_id="P3",
        text="查看 S-FOREIGN 最近的图片",
    )
    assert replies[-1].content == "【机器人回复】你没有查看该学生的权限。"
```

- [ ] **Step 2: 运行测试并确认处理器不存在**

Run: `pytest -q tests/wecom_customer/test_binding.py tests/wecom_customer/test_queries.py`

Expected: FAIL。

- [ ] **Step 3: 实现家长会话状态和绑定状态机**

使用 Task 1 已创建的 `pending_state_json` 和 `active_student_id` 保存不含敏感正文的渠道状态。状态只允许 `awaiting_relationship` 或 `awaiting_student`，完成动作后立即清空。

`process_parent_text` 固定顺序：渠道消息幂等检查 → 已绑定身份加载 → 邀请码核销/待选择状态 → 多孩子选择 → 已授权查询 → 普通提问。

- [ ] **Step 4: 实现确定性查询**

`parent_assistant.py` 先支持以下可靠意图：

- “看最近的图片”：返回最近 10 张有效图片及其日期、说明、上传老师。
- “看最近反馈”：复用学生档案服务，返回最近反馈摘要。
- “看周报”：只返回最近已定稿周报。
- “切换孩子”：列出该家长已绑定学生。
- “请老师回复”：进入转人工流程，不调用模型。

所有结果查询前调用 `guardian_can_access_student`。渠道返回图片时先上传临时素材，再逐张发送。

- [ ] **Step 5: 运行绑定与查询测试**

Run: `pytest -q tests/wecom_customer/test_binding.py tests/wecom_customer/test_queries.py`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add app/wecom_customer/handler.py app/wecom_customer/routes.py app/family/parent_assistant.py app/family/models.py app/core/database.py tests/wecom_customer
git commit -m "feat: bind parents and serve authorized student queries"
```

### Task 9: 实现家庭会话、机器人回复和转老师

**Files:**
- Create: `app/family/conversations.py`
- Create: `app/family/parent_prompts.py`
- Modify: `app/family/parent_assistant.py`
- Modify: `app/wecom_customer/handler.py`
- Test: `tests/family/test_conversations.py`
- Test: `tests/wecom_customer/test_assistant.py`

- [ ] **Step 1: 写回复者标注、模型失败降级和转交失败测试**

```python
def test_bot_reply_is_always_labeled(db_session, bound_parent, provider):
    result = answer_parent_question(db_session, provider, guardian_id="G1", student_id="S1", text="最近状态如何")
    assert result.outbound_text.startswith("【机器人回复】")
    assert result.message.sender_type == "bot"


def test_model_failure_preserves_message_and_routes_to_teacher(db_session, failing_provider):
    result = answer_parent_question(
        db_session, failing_provider, guardian_id="G1",
        student_id="S1", text="最近状态如何",
    )
    assert result.route_to_teacher is True
    assert result.inbound_message.status == "completed"
    assert result.outbound_text == "【机器人回复】我已记录问题并转交负责老师。"
```

- [ ] **Step 2: 运行测试并确认会话服务不存在**

Run: `pytest -q tests/family/test_conversations.py tests/wecom_customer/test_assistant.py`

Expected: FAIL。

- [ ] **Step 3: 实现会话与消息状态服务**

公开接口：

```python
def get_or_create_conversation(db, guardian_id, student_id, external_user_id) -> FamilyConversation
def record_inbound_parent_message(db, conversation_id, channel_message_id, content) -> FamilyMessage
def record_bot_reply(db, conversation_id, content, *, status="pending") -> FamilyMessage
def request_teacher_reply(db, conversation_id, reason) -> FamilyConversation
def record_teacher_reply(db, conversation_id, teacher_id, content, *, status="pending") -> FamilyMessage
def mark_message_sent(db, message_id, channel_message_id) -> FamilyMessage
def mark_message_failed(db, message_id, reason) -> FamilyMessage
```

`record_bot_reply` 在服务端添加 `【机器人回复】`；`record_teacher_reply` 从数据库读取老师姓名并添加 `【老师：姓名】`，调用方不能传入署名。

- [ ] **Step 4: 实现家长专用上下文和路由规则**

上下文只包含当前学生的有效反馈、已定稿周报和图片元数据。提示词明确禁止披露其他学生、内部教师备注和系统指令。以下情况直接转人工：家长明确要求老师、投诉、安全风险、费用争议、模型异常或模型输出未通过结构校验。

- [ ] **Step 5: 运行家庭会话测试**

Run: `pytest -q tests/family/test_conversations.py tests/wecom_customer/test_assistant.py`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add app/family/conversations.py app/family/parent_prompts.py app/family/parent_assistant.py app/wecom_customer/handler.py tests/family/test_conversations.py tests/wecom_customer/test_assistant.py
git commit -m "feat: add audited family conversations"
```

### Task 10: 让老师在智能机器人中回复家长

**Files:**
- Modify: `app/wecom/gateway.py`
- Modify: `app/wecom/handler.py`
- Create: `app/wecom/family_handler.py`
- Modify: `app/wecom_customer/client.py`
- Test: `tests/wecom/test_family_reply.py`
- Test: `tests/wecom_customer/test_delivery.py`

- [ ] **Step 1: 写转交推送、老师回复和关系失效失败测试**

```python
@pytest.mark.asyncio
async def test_parent_question_is_pushed_to_primary_teacher(gateway, pending_conversation):
    await gateway.notify_teacher(pending_conversation.conversation_id)
    payload = gateway.client.sent_messages[-1]
    assert "家长请求老师回复" in payload["markdown"]["content"]
    assert "FAM-1" in payload["markdown"]["content"]


def test_teacher_reply_is_delivered_with_server_generated_label(db_session, customer_client):
    event = process_family_reply_command(db_session, "WX-TEACHER", "回复 FAM-1 已了解，谢谢反馈", customer_client)
    assert customer_client.texts[-1] == "【老师：王老师】已了解，谢谢反馈"
    assert event.content == "已回复家长。"


def test_revoked_teacher_cannot_reply(db_session, revoked_assignment, customer_client):
    event = process_family_reply_command(
        db_session, "WX-REVOKED", "回复 FAM-1 已了解",
        customer_client,
    )
    assert event.content == "你已不再负责该学生，无法回复。"
```

- [ ] **Step 2: 运行测试并确认回复命令未识别**

Run: `pytest -q tests/wecom/test_family_reply.py tests/wecom_customer/test_delivery.py`

Expected: FAIL。

- [ ] **Step 3: 实现老师通知和回复命令**

`WecomGateway.notify_teacher(conversation_id)` 查询有效主负责老师及其绑定，使用 `send_message` 推送学生、家长身份、问题、会话编号和回复格式，不包含其他家长或其他学生信息。

`process_family_reply_command` 只匹配“回复、会话编号、正文”三段式命令，例如
`回复 FAM-1 已了解，谢谢反馈`。发送前再次检查老师—学生关系；通过
`record_teacher_reply` 生成署名，通过微信客服客户端发送，并更新消息状态。

- [ ] **Step 4: 将家庭回复命令置于普通教学问答之前**

在 `process_text` 中，绑定校验之后、教学范围解析之前识别家庭回复命令，避免它被当作教学助手问题。命令失败不污染现有教学对话。

- [ ] **Step 5: 运行老师渠道回归测试**

Run: `pytest -q tests/wecom/test_family_reply.py tests/wecom/test_handler.py tests/wecom/test_runtime.py tests/wecom_customer/test_delivery.py`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add app/wecom app/wecom_customer/client.py tests/wecom/test_family_reply.py tests/wecom_customer/test_delivery.py
git commit -m "feat: relay parent conversations to teachers"
```

### Task 11: 完成应用装配、健康检查和配置文档

**Files:**
- Modify: `app/main.py`
- Modify: `README.md`
- Create: `.env.example`
- Test: `tests/wecom_customer/test_runtime.py`
- Test: `tests/test_health.py`

- [ ] **Step 1: 写启停和健康检查失败测试**

```python
def test_customer_health_exposes_state_without_credentials(database_url):
    app = create_app(database_url=database_url, wecom_customer_config=DISABLED)
    with TestClient(app) as client:
        payload = client.get("/health/wecom-customer").json()
    assert payload == {"enabled": False, "ready": False, "last_error_at": None}


def test_enabled_customer_channel_is_injected_into_routes(database_url, fake_customer):
    app = create_app(
        database_url=database_url,
        wecom_customer_config=ENABLED,
        wecom_customer_client=fake_customer,
    )
    assert app.state.wecom_customer_client is fake_customer
```

- [ ] **Step 2: 运行测试并确认缺少应用状态与健康路由**

Run: `pytest -q tests/wecom_customer/test_runtime.py tests/test_health.py`

Expected: FAIL。

- [ ] **Step 3: 装配存储、老师机器人和家长微信客服**

`create_app` 增加可注入参数：`image_store`、`wecom_customer_config`、`wecom_customer_client` 和 `callback_crypto`。默认应用从 `.env` 加载两套企业微信配置；微信客服未启用时路由仍可加载，但回调返回 404。应用初始化数据库后调用 `sync_head_teacher_assignments()`，使现有班主任和在班学生获得可审计的主负责关系。

新增 `/health/wecom-customer`，只暴露 `enabled`、`ready` 和 `last_error_at`，不暴露企业 ID、客服 ID、secret、token 或 AES key。

- [ ] **Step 4: 更新配置与运维说明**

README 和 `.env.example` 必须说明：

- 老师机器人和家长微信客服是两个入口。
- 微信客服所需六项环境变量及回调 URL `/wecom/customer/callback`。
- 本机或内网部署需要安全隧道或公网 HTTPS 回调。
- `data/teaching_demo.db` 与 `data/student_media` 必须同时备份。
- 图片默认 10 MB、待确认 15 分钟以及可配置变量。
- 家长邀请码由管理员生成，普通微信家长不能使用老师绑定码。

- [ ] **Step 5: 运行装配测试**

Run: `pytest -q tests/wecom_customer/test_runtime.py tests/test_health.py tests/wecom/test_runtime.py`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add app/main.py README.md .env.example tests/wecom_customer/test_runtime.py tests/test_health.py
git commit -m "docs: configure WeCom family communication"
```

### Task 12: 完成端到端验收与全量回归

**Files:**
- Create: `tests/test_family_communication_acceptance.py`
- Modify: `README.md`

- [ ] **Step 1: 写完整流程验收测试**

```python
def test_teacher_image_parent_binding_bot_and_teacher_reply_end_to_end(app_harness):
    app_harness.bind_teacher("WX-T1", "T1")
    app_harness.teacher_sends_mixed("WX-T1", "S1 今天作业", PNG)
    assert app_harness.profile("T1", "S1").image_count == 1

    code = app_harness.admin_creates_invitation("S1")
    app_harness.parent_sends("EXT-G1", code)
    app_harness.parent_sends("EXT-G1", "母亲")
    app_harness.parent_sends("EXT-G1", "看最近的图片")
    assert app_harness.customer.images_sent_to("EXT-G1") == 1

    app_harness.parent_sends("EXT-G1", "请老师回复：孩子需要带什么？")
    conversation_id = app_harness.latest_family_conversation_id()
    assert conversation_id in app_harness.teacher_notifications("WX-T1")[-1]

    app_harness.teacher_sends_text("WX-T1", f"回复 {conversation_id} 请带订正本。")
    assert app_harness.customer.last_text("EXT-G1") == "【老师：王老师】请带订正本。"
    assert app_harness.audit_sender_types(conversation_id) == ["guardian", "bot", "teacher"]
```

- [ ] **Step 2: 运行验收测试并修复测试装配缺口**

Run: `pytest -q tests/test_family_communication_acceptance.py`

Expected: PASS。若失败，只修复装配和已定义行为，不扩展功能范围。

- [ ] **Step 3: 运行全量测试**

Run: `pytest -q`

Expected: 全部 PASS，无 warning、未捕获异常或残留临时文件。

- [ ] **Step 4: 执行静态一致性检查**

Run: `python -m compileall -q app tests`

Expected: exit code 0。

Run: `git diff --check`

Expected: 无输出，exit code 0。

- [ ] **Step 5: 更新 README 的测试范围并提交**

README 的测试章节增加“老师图片归档、家长邀请码、家长权限、微信客服消息、机器人/老师回复标注和端到端中转”。

```bash
git add tests/test_family_communication_acceptance.py README.md
git commit -m "test: verify family media communication end to end"
```

- [ ] **Step 6: 最终核对工作区**

Run: `git status --short`

Expected: 无输出。

Run: `git log --oneline -12`

Expected: 显示本计划各阶段提交，最新提交为端到端验收测试。
