from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ConversationType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CONVERSATION_TYPE_UNSPECIFIED: _ClassVar[ConversationType]
    SINGLE_CHAT: _ClassVar[ConversationType]
    GROUP_CHAT: _ClassVar[ConversationType]
CONVERSATION_TYPE_UNSPECIFIED: ConversationType
SINGLE_CHAT: ConversationType
GROUP_CHAT: ConversationType

class ConnectRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ServerEvent(_message.Message):
    __slots__ = ("event_id", "timestamp", "received_message")
    EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    RECEIVED_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    event_id: str
    timestamp: int
    received_message: IncomingMessage
    def __init__(self, event_id: _Optional[str] = ..., timestamp: _Optional[int] = ..., received_message: _Optional[_Union[IncomingMessage, _Mapping]] = ...) -> None: ...

class IncomingMessage(_message.Message):
    __slots__ = ("message_id", "chat", "sender", "content")
    MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    CHAT_FIELD_NUMBER: _ClassVar[int]
    SENDER_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    message_id: str
    chat: ChatContext
    sender: Sender
    content: IncomingContent
    def __init__(self, message_id: _Optional[str] = ..., chat: _Optional[_Union[ChatContext, _Mapping]] = ..., sender: _Optional[_Union[Sender, _Mapping]] = ..., content: _Optional[_Union[IncomingContent, _Mapping]] = ...) -> None: ...

class SendMessageRequest(_message.Message):
    __slots__ = ("target", "content", "client_message_id")
    TARGET_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    CLIENT_MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    target: ChatTarget
    content: OutgoingContent
    client_message_id: str
    def __init__(self, target: _Optional[_Union[ChatTarget, _Mapping]] = ..., content: _Optional[_Union[OutgoingContent, _Mapping]] = ..., client_message_id: _Optional[str] = ...) -> None: ...

class SendMessageResponse(_message.Message):
    __slots__ = ("client_message_id", "accepted", "error")
    CLIENT_MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    ACCEPTED_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    client_message_id: str
    accepted: SendAccepted
    error: ErrorInfo
    def __init__(self, client_message_id: _Optional[str] = ..., accepted: _Optional[_Union[SendAccepted, _Mapping]] = ..., error: _Optional[_Union[ErrorInfo, _Mapping]] = ...) -> None: ...

class ChatContext(_message.Message):
    __slots__ = ("conversation_type", "conversation_id", "conversation_name")
    CONVERSATION_TYPE_FIELD_NUMBER: _ClassVar[int]
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    CONVERSATION_NAME_FIELD_NUMBER: _ClassVar[int]
    conversation_type: ConversationType
    conversation_id: str
    conversation_name: str
    def __init__(self, conversation_type: _Optional[_Union[ConversationType, str]] = ..., conversation_id: _Optional[str] = ..., conversation_name: _Optional[str] = ...) -> None: ...

class ChatTarget(_message.Message):
    __slots__ = ("chat", "bound_user")
    CHAT_FIELD_NUMBER: _ClassVar[int]
    BOUND_USER_FIELD_NUMBER: _ClassVar[int]
    chat: ChatContext
    bound_user: BoundUserTarget
    def __init__(self, chat: _Optional[_Union[ChatContext, _Mapping]] = ..., bound_user: _Optional[_Union[BoundUserTarget, _Mapping]] = ...) -> None: ...

class BoundUserTarget(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class Sender(_message.Message):
    __slots__ = ("open_id", "nickname")
    OPEN_ID_FIELD_NUMBER: _ClassVar[int]
    NICKNAME_FIELD_NUMBER: _ClassVar[int]
    open_id: str
    nickname: str
    def __init__(self, open_id: _Optional[str] = ..., nickname: _Optional[str] = ...) -> None: ...

class IncomingContent(_message.Message):
    __slots__ = ("text", "markdown", "image", "file")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    MARKDOWN_FIELD_NUMBER: _ClassVar[int]
    IMAGE_FIELD_NUMBER: _ClassVar[int]
    FILE_FIELD_NUMBER: _ClassVar[int]
    text: TextContent
    markdown: MarkdownContent
    image: ImageContent
    file: FileContent
    def __init__(self, text: _Optional[_Union[TextContent, _Mapping]] = ..., markdown: _Optional[_Union[MarkdownContent, _Mapping]] = ..., image: _Optional[_Union[ImageContent, _Mapping]] = ..., file: _Optional[_Union[FileContent, _Mapping]] = ...) -> None: ...

class OutgoingContent(_message.Message):
    __slots__ = ("text", "markdown", "image", "file")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    MARKDOWN_FIELD_NUMBER: _ClassVar[int]
    IMAGE_FIELD_NUMBER: _ClassVar[int]
    FILE_FIELD_NUMBER: _ClassVar[int]
    text: TextContent
    markdown: MarkdownContent
    image: ImageContent
    file: FileContent
    def __init__(self, text: _Optional[_Union[TextContent, _Mapping]] = ..., markdown: _Optional[_Union[MarkdownContent, _Mapping]] = ..., image: _Optional[_Union[ImageContent, _Mapping]] = ..., file: _Optional[_Union[FileContent, _Mapping]] = ...) -> None: ...

class SendAccepted(_message.Message):
    __slots__ = ("message_id", "accepted_at")
    MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    ACCEPTED_AT_FIELD_NUMBER: _ClassVar[int]
    message_id: str
    accepted_at: int
    def __init__(self, message_id: _Optional[str] = ..., accepted_at: _Optional[int] = ...) -> None: ...

class ErrorInfo(_message.Message):
    __slots__ = ("code", "message")
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    code: int
    message: str
    def __init__(self, code: _Optional[int] = ..., message: _Optional[str] = ...) -> None: ...

class TextContent(_message.Message):
    __slots__ = ("content",)
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    content: str
    def __init__(self, content: _Optional[str] = ...) -> None: ...

class MarkdownContent(_message.Message):
    __slots__ = ("title", "text")
    TITLE_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    title: str
    text: str
    def __init__(self, title: _Optional[str] = ..., text: _Optional[str] = ...) -> None: ...

class ImageContent(_message.Message):
    __slots__ = ("url", "secret_key", "size", "file_length")
    URL_FIELD_NUMBER: _ClassVar[int]
    SECRET_KEY_FIELD_NUMBER: _ClassVar[int]
    SIZE_FIELD_NUMBER: _ClassVar[int]
    FILE_LENGTH_FIELD_NUMBER: _ClassVar[int]
    url: str
    secret_key: str
    size: ImageSize
    file_length: int
    def __init__(self, url: _Optional[str] = ..., secret_key: _Optional[str] = ..., size: _Optional[_Union[ImageSize, _Mapping]] = ..., file_length: _Optional[int] = ...) -> None: ...

class FileContent(_message.Message):
    __slots__ = ("url", "secret_key", "file_name", "file_length")
    URL_FIELD_NUMBER: _ClassVar[int]
    SECRET_KEY_FIELD_NUMBER: _ClassVar[int]
    FILE_NAME_FIELD_NUMBER: _ClassVar[int]
    FILE_LENGTH_FIELD_NUMBER: _ClassVar[int]
    url: str
    secret_key: str
    file_name: str
    file_length: int
    def __init__(self, url: _Optional[str] = ..., secret_key: _Optional[str] = ..., file_name: _Optional[str] = ..., file_length: _Optional[int] = ...) -> None: ...

class ImageSize(_message.Message):
    __slots__ = ("h", "w")
    H_FIELD_NUMBER: _ClassVar[int]
    W_FIELD_NUMBER: _ClassVar[int]
    h: int
    w: int
    def __init__(self, h: _Optional[int] = ..., w: _Optional[int] = ...) -> None: ...
