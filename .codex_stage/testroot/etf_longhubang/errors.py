class ProducerError(RuntimeError):
    exit_code = 1
    reason_code: str | None = None


class LockBusyError(ProducerError):
    exit_code = 60


class SourceUnavailableError(ProducerError):
    exit_code = 20
    reason_code = "source_unavailable"


class InsufficientDataError(ProducerError):
    exit_code = 30
    reason_code = "insufficient_data"


class PackageValidationError(ProducerError):
    exit_code = 40


class PublicationError(ProducerError):
    exit_code = 50

