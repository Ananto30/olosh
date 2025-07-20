from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Heartbeat(_message.Message):
    __slots__ = ("timestamp", "running_containers", "cpu_percent", "mem_percent")
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    RUNNING_CONTAINERS_FIELD_NUMBER: _ClassVar[int]
    CPU_PERCENT_FIELD_NUMBER: _ClassVar[int]
    MEM_PERCENT_FIELD_NUMBER: _ClassVar[int]
    timestamp: int
    running_containers: _containers.RepeatedScalarFieldContainer[str]
    cpu_percent: float
    mem_percent: float
    def __init__(self, timestamp: _Optional[int] = ..., running_containers: _Optional[_Iterable[str]] = ..., cpu_percent: _Optional[float] = ..., mem_percent: _Optional[float] = ...) -> None: ...

class JobResult(_message.Message):
    __slots__ = ("job_id", "status", "container_id", "detail")
    class Status(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        PENDING: _ClassVar[JobResult.Status]
        RUNNING: _ClassVar[JobResult.Status]
        FINISHED: _ClassVar[JobResult.Status]
        FAILED: _ClassVar[JobResult.Status]
    PENDING: JobResult.Status
    RUNNING: JobResult.Status
    FINISHED: JobResult.Status
    FAILED: JobResult.Status
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    CONTAINER_ID_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    status: JobResult.Status
    container_id: str
    detail: str
    def __init__(self, job_id: _Optional[str] = ..., status: _Optional[_Union[JobResult.Status, str]] = ..., container_id: _Optional[str] = ..., detail: _Optional[str] = ...) -> None: ...

class LogResponse(_message.Message):
    __slots__ = ("job_id", "content")
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    content: str
    def __init__(self, job_id: _Optional[str] = ..., content: _Optional[str] = ...) -> None: ...

class JobAssignment(_message.Message):
    __slots__ = ("job_id", "docker_image", "run_params")
    class RunParamsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    DOCKER_IMAGE_FIELD_NUMBER: _ClassVar[int]
    RUN_PARAMS_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    docker_image: bytes
    run_params: _containers.ScalarMap[str, str]
    def __init__(self, job_id: _Optional[str] = ..., docker_image: _Optional[bytes] = ..., run_params: _Optional[_Mapping[str, str]] = ...) -> None: ...

class LogRequest(_message.Message):
    __slots__ = ("job_id",)
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    def __init__(self, job_id: _Optional[str] = ...) -> None: ...

class Ack(_message.Message):
    __slots__ = ("message",)
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    message: str
    def __init__(self, message: _Optional[str] = ...) -> None: ...

class Error(_message.Message):
    __slots__ = ("code", "message")
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    code: str
    message: str
    def __init__(self, code: _Optional[str] = ..., message: _Optional[str] = ...) -> None: ...

class AgentMessage(_message.Message):
    __slots__ = ("heartbeat", "job_result", "log_response", "ack", "error")
    HEARTBEAT_FIELD_NUMBER: _ClassVar[int]
    JOB_RESULT_FIELD_NUMBER: _ClassVar[int]
    LOG_RESPONSE_FIELD_NUMBER: _ClassVar[int]
    ACK_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    heartbeat: Heartbeat
    job_result: JobResult
    log_response: LogResponse
    ack: Ack
    error: Error
    def __init__(self, heartbeat: _Optional[_Union[Heartbeat, _Mapping]] = ..., job_result: _Optional[_Union[JobResult, _Mapping]] = ..., log_response: _Optional[_Union[LogResponse, _Mapping]] = ..., ack: _Optional[_Union[Ack, _Mapping]] = ..., error: _Optional[_Union[Error, _Mapping]] = ...) -> None: ...

class OrchestratorMessage(_message.Message):
    __slots__ = ("job_assignment", "log_request", "ack", "error")
    JOB_ASSIGNMENT_FIELD_NUMBER: _ClassVar[int]
    LOG_REQUEST_FIELD_NUMBER: _ClassVar[int]
    ACK_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    job_assignment: JobAssignment
    log_request: LogRequest
    ack: Ack
    error: Error
    def __init__(self, job_assignment: _Optional[_Union[JobAssignment, _Mapping]] = ..., log_request: _Optional[_Union[LogRequest, _Mapping]] = ..., ack: _Optional[_Union[Ack, _Mapping]] = ..., error: _Optional[_Union[Error, _Mapping]] = ...) -> None: ...
