from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Heartbeat(_message.Message):
    __slots__ = ("timestamp", "running_containers")
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    RUNNING_CONTAINERS_FIELD_NUMBER: _ClassVar[int]
    timestamp: int
    running_containers: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, timestamp: _Optional[int] = ..., running_containers: _Optional[_Iterable[str]] = ...) -> None: ...

class JobAssignment(_message.Message):
    __slots__ = ("job_id", "dockerfile", "build_args", "run_params")
    class BuildArgsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    class RunParamsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    DOCKERFILE_FIELD_NUMBER: _ClassVar[int]
    BUILD_ARGS_FIELD_NUMBER: _ClassVar[int]
    RUN_PARAMS_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    dockerfile: str
    build_args: _containers.ScalarMap[str, str]
    run_params: _containers.ScalarMap[str, str]
    def __init__(self, job_id: _Optional[str] = ..., dockerfile: _Optional[str] = ..., build_args: _Optional[_Mapping[str, str]] = ..., run_params: _Optional[_Mapping[str, str]] = ...) -> None: ...

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

class LogRequest(_message.Message):
    __slots__ = ("job_id",)
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    def __init__(self, job_id: _Optional[str] = ...) -> None: ...

class LogResponse(_message.Message):
    __slots__ = ("job_id", "content")
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    content: str
    def __init__(self, job_id: _Optional[str] = ..., content: _Optional[str] = ...) -> None: ...

class AgentMessage(_message.Message):
    __slots__ = ("heartbeat", "job_result", "log_response")
    HEARTBEAT_FIELD_NUMBER: _ClassVar[int]
    JOB_RESULT_FIELD_NUMBER: _ClassVar[int]
    LOG_RESPONSE_FIELD_NUMBER: _ClassVar[int]
    heartbeat: Heartbeat
    job_result: JobResult
    log_response: LogResponse
    def __init__(self, heartbeat: _Optional[_Union[Heartbeat, _Mapping]] = ..., job_result: _Optional[_Union[JobResult, _Mapping]] = ..., log_response: _Optional[_Union[LogResponse, _Mapping]] = ...) -> None: ...

class OrchestratorMessage(_message.Message):
    __slots__ = ("job_assignment", "log_request")
    JOB_ASSIGNMENT_FIELD_NUMBER: _ClassVar[int]
    LOG_REQUEST_FIELD_NUMBER: _ClassVar[int]
    job_assignment: JobAssignment
    log_request: LogRequest
    def __init__(self, job_assignment: _Optional[_Union[JobAssignment, _Mapping]] = ..., log_request: _Optional[_Union[LogRequest, _Mapping]] = ...) -> None: ...
