from __future__ import annotations

from abc import ABC, abstractmethod

from job_assistant.models import BatchStats, RawRecord


class VacancyConnector(ABC):
    @abstractmethod
    def fetch(self) -> tuple[list[RawRecord], BatchStats]:
        raise NotImplementedError
