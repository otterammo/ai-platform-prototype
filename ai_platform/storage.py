from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, TypeAlias

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, create_engine, select
from sqlalchemy import delete as sql_delete
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import JSON

from .events import (
    CORRELATION_ID_ANNOTATION,
    CORRELATION_ID_STATUS_KEY,
    EventContext,
    correlation_id_from_manifest,
    new_correlation_id,
)
from .resources import (
    AgentResource,
    FleetResource,
    ResourceKind,
    dump_resource,
    parse_resource,
    resource_key,
)

DEFAULT_DB_URL = "sqlite:///./platform.db"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class ResourceRecord(Base):
    __tablename__ = "resources"
    __table_args__ = (UniqueConstraint("kind", "namespace", "name", name="uq_resource_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    api_version: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    namespace: Mapped[str] = mapped_column(String(255), nullable=False, default="", index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class EventRecord(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    resource_kind: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    namespace: Mapped[str] = mapped_column(String(255), nullable=False, default="", index=True)
    resource_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    message: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)


class ArtifactRecord(Base):
    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    namespace: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    mission: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    agent: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    path: Mapped[str] = mapped_column(String(2000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


JsonDict: TypeAlias = dict[str, Any]
JsonDictList: TypeAlias = list[JsonDict]
ResourceRecordList: TypeAlias = list[ResourceRecord]


def make_engine(database_url: str = DEFAULT_DB_URL) -> Engine:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args)


class ResourceStore:
    def __init__(self, database_url: str = DEFAULT_DB_URL, platform_root: str | Path = ".platform") -> None:
        self.database_url = database_url
        self.platform_root = Path(platform_root).expanduser().resolve()
        self.engine = make_engine(database_url)
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def apply(self, manifest: JsonDict, event_context: EventContext | None = None) -> JsonDict:
        resource = parse_resource(manifest)
        kind, namespace, name = resource_key(resource.kind, resource.metadata.name, resource.metadata.namespace)
        correlation_id = event_context.correlation_id if event_context else None
        if kind == ResourceKind.MISSION.value:
            correlation_id = new_correlation_id()
        elif correlation_id is None:
            correlation_id = correlation_id_from_manifest(manifest)
        if correlation_id:
            resource.metadata.annotations[CORRELATION_ID_ANNOTATION] = correlation_id
            resource.status.data[CORRELATION_ID_STATUS_KEY] = correlation_id
        with self.session() as session:
            record = session.scalar(
                select(ResourceRecord).where(
                    ResourceRecord.kind == kind,
                    ResourceRecord.namespace == namespace,
                    ResourceRecord.name == name,
                )
            )
            event_type = f"{kind}Created"
            if record:
                resource.metadata.generation = record.generation + 1
                resource.status = parse_resource(record.manifest).status
                if correlation_id:
                    resource.metadata.annotations[CORRELATION_ID_ANNOTATION] = correlation_id
                    resource.status.data[CORRELATION_ID_STATUS_KEY] = correlation_id
                resource.status.observedGeneration = min(
                    resource.status.observedGeneration,
                    resource.metadata.generation,
                )
                event_type = f"{kind}Updated"
                record.generation = resource.metadata.generation
                record.api_version = resource.apiVersion
                record.manifest = dump_resource(resource)
                record.updated_at = utcnow()
            else:
                resource.metadata.generation = 1
                resource.status.observedGeneration = 0
                record = ResourceRecord(
                    api_version=resource.apiVersion,
                    kind=kind,
                    namespace=namespace,
                    name=name,
                    generation=resource.metadata.generation,
                    manifest=dump_resource(resource),
                )
                session.add(record)
            event_payload = self._structured_payload(
                {"generation": resource.metadata.generation},
                event_context,
                default_controller="ResourceStore",
                default_action=event_type,
                default_reason="ResourceApplied",
                namespace=namespace,
                correlation_id=correlation_id,
                mission=name if kind == ResourceKind.MISSION.value else None,
            )
            session.add(
                EventRecord(
                    type=event_type,
                    resource_kind=kind,
                    namespace=namespace,
                    resource_name=name,
                    message=f"{kind} {self.display_name(namespace, name)} applied",
                    payload=event_payload,
                )
            )
            return record.manifest

    def get(self, kind: str | ResourceKind, name: str, namespace: str | None = None) -> JsonDict | None:
        kind_value, namespace_value, name_value = resource_key(kind, name, namespace)
        with self.session() as session:
            record = session.scalar(
                select(ResourceRecord).where(
                    ResourceRecord.kind == kind_value,
                    ResourceRecord.namespace == namespace_value,
                    ResourceRecord.name == name_value,
                )
            )
            return record.manifest if record else None

    def list(self, kind: str | ResourceKind | None = None, namespace: str | None = None) -> JsonDictList:
        with self.session() as session:
            statement = select(ResourceRecord)
            if kind:
                statement = statement.where(ResourceRecord.kind == ResourceKind(kind).value)
            if namespace is not None:
                statement = statement.where(ResourceRecord.namespace == namespace)
            statement = statement.order_by(ResourceRecord.kind, ResourceRecord.namespace, ResourceRecord.name)
            return [record.manifest for record in session.scalars(statement).all()]

    def delete(self, kind: str | ResourceKind, name: str, namespace: str | None = None) -> bool:
        kind_value, namespace_value, name_value = resource_key(kind, name, namespace)
        with self.session() as session:
            record = session.scalar(
                select(ResourceRecord).where(
                    ResourceRecord.kind == kind_value,
                    ResourceRecord.namespace == namespace_value,
                    ResourceRecord.name == name_value,
                )
            )
            if not record:
                return False

            deleted_ids: set[int] = set()
            if kind_value == ResourceKind.WORKSPACE.value:
                self._delete_workspace(session, record, deleted_ids)
            elif kind_value == ResourceKind.MISSION.value:
                self._delete_mission(session, record, deleted_ids)
            elif kind_value == ResourceKind.FLEET.value:
                self._delete_fleet(session, record, deleted_ids)
            else:
                self._delete_record(session, record, deleted_ids)
            return True

    def _delete_workspace(self, session: Session, record: ResourceRecord, deleted_ids: set[int]) -> None:
        workspace_name = record.name
        children = session.scalars(select(ResourceRecord).where(ResourceRecord.namespace == workspace_name)).all()
        delete_order = {
            ResourceKind.AGENT.value: 0,
            ResourceKind.FLEET.value: 1,
            ResourceKind.MISSION.value: 2,
        }
        for child in sorted(children, key=lambda item: delete_order.get(item.kind, 99)):
            self._delete_record(session, child, deleted_ids, cascade="workspace")
        session.execute(sql_delete(ArtifactRecord).where(ArtifactRecord.namespace == workspace_name))
        self._delete_record(session, record, deleted_ids)

    def _delete_mission(self, session: Session, record: ResourceRecord, deleted_ids: set[int]) -> None:
        namespace = record.namespace
        mission_name = record.name
        agents = self._owned_agents_for_mission(session, namespace, mission_name)
        fleets = self._owned_fleets_for_mission(session, namespace, mission_name)
        for agent in agents:
            self._delete_record(session, agent, deleted_ids, cascade="mission")
        for fleet in fleets:
            self._delete_record(session, fleet, deleted_ids, cascade="mission")
        session.execute(
            sql_delete(ArtifactRecord).where(
                ArtifactRecord.namespace == namespace,
                ArtifactRecord.mission == mission_name,
            )
        )
        self._delete_record(session, record, deleted_ids)

    def _delete_fleet(self, session: Session, record: ResourceRecord, deleted_ids: set[int]) -> None:
        for agent in self._owned_agents_for_fleet(session, record.namespace, record.name):
            self._delete_record(session, agent, deleted_ids, cascade="fleet")
        self._delete_record(session, record, deleted_ids)

    def _delete_record(
        self,
        session: Session,
        record: ResourceRecord,
        deleted_ids: set[int],
        cascade: str | None = None,
    ) -> None:
        if record.id in deleted_ids:
            return
        deleted_ids.add(record.id)
        message = f"{record.kind} {self.display_name(record.namespace, record.name)} deleted"
        payload: dict[str, Any] = {}
        if cascade:
            message = f"{message} by {cascade} cascade"
            payload["cascade"] = cascade
        session.delete(record)
        session.add(
            EventRecord(
                type=f"{record.kind}Deleted",
                resource_kind=record.kind,
                namespace=record.namespace,
                resource_name=record.name,
                message=message,
                payload=payload,
            )
        )

    def _owned_fleets_for_mission(self, session: Session, namespace: str, mission: str) -> ResourceRecordList:
        records = session.scalars(
            select(ResourceRecord).where(
                ResourceRecord.kind == ResourceKind.FLEET.value,
                ResourceRecord.namespace == namespace,
            )
        ).all()
        owned: ResourceRecordList = []
        for record in records:
            resource = parse_resource(record.manifest)
            if isinstance(resource, FleetResource) and resource.spec.mission == mission:
                owned.append(record)
        return owned

    def _owned_agents_for_mission(self, session: Session, namespace: str, mission: str) -> ResourceRecordList:
        records = session.scalars(
            select(ResourceRecord).where(
                ResourceRecord.kind == ResourceKind.AGENT.value,
                ResourceRecord.namespace == namespace,
            )
        ).all()
        owned: ResourceRecordList = []
        for record in records:
            resource = parse_resource(record.manifest)
            if isinstance(resource, AgentResource) and resource.spec.mission == mission:
                owned.append(record)
        return owned

    def _owned_agents_for_fleet(self, session: Session, namespace: str, fleet: str) -> ResourceRecordList:
        records = session.scalars(
            select(ResourceRecord).where(
                ResourceRecord.kind == ResourceKind.AGENT.value,
                ResourceRecord.namespace == namespace,
            )
        ).all()
        owned: ResourceRecordList = []
        for record in records:
            resource = parse_resource(record.manifest)
            if isinstance(resource, AgentResource) and resource.spec.fleet == fleet:
                owned.append(record)
        return owned

    def update_status(
        self,
        kind: str | ResourceKind,
        name: str,
        namespace: str | None,
        phase: str,
        message: str | None = None,
        data: dict[str, Any] | None = None,
        event_type: str | None = None,
        event_context: EventContext | None = None,
    ) -> JsonDict:
        kind_value, namespace_value, name_value = resource_key(kind, name, namespace)
        with self.session() as session:
            record = session.scalar(
                select(ResourceRecord).where(
                    ResourceRecord.kind == kind_value,
                    ResourceRecord.namespace == namespace_value,
                    ResourceRecord.name == name_value,
                )
            )
            if not record:
                raise KeyError(f"{kind_value} {self.display_name(namespace_value, name_value)} not found")
            manifest = dict(record.manifest)
            metadata = dict(manifest.get("metadata") or {})
            annotations = dict(metadata.get("annotations") or {})
            status = dict(manifest.get("status") or {})
            existing_data = dict(status.get("data") or {})
            correlation_id = (
                (event_context.correlation_id if event_context else None)
                or annotations.get(CORRELATION_ID_ANNOTATION)
                or existing_data.get(CORRELATION_ID_STATUS_KEY)
            )
            status["phase"] = phase
            status["observedGeneration"] = record.generation
            if message is not None:
                status["message"] = message
            if data or correlation_id:
                merged = dict(existing_data)
                if correlation_id:
                    merged[CORRELATION_ID_STATUS_KEY] = correlation_id
                if data:
                    merged.update(data)
                status["data"] = merged
            status["conditions"] = self._conditions_for_phase(
                kind_value,
                phase,
                status.get("conditions") or [],
                reason=event_context.reason if event_context else phase,
                message=message,
            )
            manifest["status"] = status
            record.manifest = manifest
            record.updated_at = utcnow()
            if event_type:
                event_payload = self._structured_payload(
                    {"phase": phase, "data": data or {}},
                    event_context,
                    default_controller="ResourceStore",
                    default_action=event_type,
                    default_reason=phase,
                    namespace=namespace_value,
                    correlation_id=correlation_id if isinstance(correlation_id, str) else None,
                    mission=name_value if kind_value == ResourceKind.MISSION.value else None,
                )
                session.add(
                    EventRecord(
                        type=event_type,
                        resource_kind=kind_value,
                        namespace=namespace_value,
                        resource_name=name_value,
                        message=message or f"{kind_value} {self.display_name(namespace_value, name_value)} is {phase}",
                        payload=event_payload,
                    )
                )
            return manifest

    def emit_event(
        self,
        event_type: str,
        resource_kind: str | ResourceKind | None = None,
        resource_name: str | None = None,
        namespace: str | None = None,
        message: str = "",
        payload: dict[str, Any] | None = None,
        event_context: EventContext | None = None,
        controller: str | None = None,
        action: str | None = None,
        reason: str | None = None,
        correlation_id: str | None = None,
    ) -> JsonDict:
        kind_value = ResourceKind(resource_kind).value if resource_kind else None
        namespace_value = namespace or ""
        event_payload = self._structured_payload(
            payload or {},
            event_context,
            default_controller=controller or "ResourceStore",
            default_action=action or event_type,
            default_reason=reason or event_type,
            namespace=namespace_value,
            correlation_id=correlation_id,
            mission=(resource_name if kind_value == ResourceKind.MISSION.value else None),
        )
        with self.session() as session:
            record = EventRecord(
                type=event_type,
                resource_kind=kind_value,
                namespace=namespace_value,
                resource_name=resource_name,
                message=message,
                payload=event_payload,
            )
            session.add(record)
            session.flush()
            return self.event_to_dict(record)

    def list_events(
        self,
        namespace: str | None = None,
        resource_kind: str | ResourceKind | None = None,
        resource_name: str | None = None,
        limit: int | None = 100,
        correlation_id: str | None = None,
        ascending: bool = False,
    ) -> JsonDictList:
        with self.session() as session:
            statement = select(EventRecord)
            if namespace is not None:
                statement = statement.where(EventRecord.namespace == namespace)
            if resource_kind is not None:
                statement = statement.where(EventRecord.resource_kind == ResourceKind(resource_kind).value)
            if resource_name is not None:
                statement = statement.where(EventRecord.resource_name == resource_name)
            if ascending:
                statement = statement.order_by(EventRecord.created_at.asc(), EventRecord.id.asc())
            else:
                statement = statement.order_by(EventRecord.created_at.desc(), EventRecord.id.desc())
            if limit is not None and correlation_id is None:
                statement = statement.limit(limit)
            records = session.scalars(statement).all()
            events = [self.event_to_dict(record) for record in records]
            if correlation_id is not None:
                events = [event for event in events if event["correlationId"] == correlation_id]
                if limit is not None:
                    events = events[:limit]
            return events

    def record_artifact(self, namespace: str, mission: str, agent: str, path: Path) -> JsonDict:
        with self.session() as session:
            record = ArtifactRecord(namespace=namespace, mission=mission, agent=agent, path=str(path))
            session.add(record)
            session.flush()
            return {
                "id": record.id,
                "namespace": record.namespace,
                "mission": record.mission,
                "agent": record.agent,
                "path": record.path,
                "createdAt": record.created_at.isoformat(),
            }

    def list_artifacts(self, namespace: str | None = None, mission: str | None = None) -> JsonDictList:
        with self.session() as session:
            statement = select(ArtifactRecord)
            if namespace is not None:
                statement = statement.where(ArtifactRecord.namespace == namespace)
            if mission is not None:
                statement = statement.where(ArtifactRecord.mission == mission)
            statement = statement.order_by(ArtifactRecord.created_at.desc(), ArtifactRecord.id.desc())
            return [
                {
                    "id": record.id,
                    "namespace": record.namespace,
                    "mission": record.mission,
                    "agent": record.agent,
                    "path": record.path,
                    "createdAt": record.created_at.isoformat(),
                }
                for record in session.scalars(statement).all()
            ]

    @staticmethod
    def event_to_dict(record: EventRecord) -> JsonDict:
        payload = record.payload or {}
        timestamp = record.created_at.isoformat()
        return {
            "id": record.id,
            "type": record.type,
            "timestamp": timestamp,
            "resourceKind": record.resource_kind,
            "namespace": record.namespace or None,
            "workspace": payload.get("workspace") or record.namespace or None,
            "resourceName": record.resource_name,
            "resource": record.resource_name,
            "controller": payload.get("controller"),
            "action": payload.get("action") or record.type,
            "reason": payload.get("reason") or record.type,
            "correlationId": payload.get("correlationId"),
            "message": record.message,
            "payload": payload,
            "createdAt": timestamp,
        }

    @staticmethod
    def display_name(namespace: str | None, name: str) -> str:
        return f"{namespace}/{name}" if namespace else name

    @staticmethod
    def _structured_payload(
        payload: dict[str, Any],
        event_context: EventContext | None,
        *,
        default_controller: str,
        default_action: str,
        default_reason: str,
        namespace: str | None,
        correlation_id: str | None,
        mission: str | None = None,
    ) -> dict[str, Any]:
        result = dict(payload)
        result["controller"] = event_context.controller if event_context else default_controller
        result["action"] = event_context.action if event_context else default_action
        result["reason"] = event_context.reason if event_context else default_reason
        result["correlationId"] = (
            event_context.correlation_id if event_context and event_context.correlation_id else correlation_id
        )
        result["workspace"] = (event_context.workspace if event_context else None) or namespace or None
        result["mission"] = (event_context.mission if event_context else None) or mission
        return result

    @staticmethod
    def _conditions_for_phase(
        kind: str,
        phase: str,
        existing_conditions: JsonDictList,
        *,
        reason: str,
        message: str | None,
    ) -> JsonDictList:
        condition_values: dict[str, bool] = {
            "Reconciling": phase == "Reconciling",
            "Running": phase in {"Reconciling", "Running"},
            "Completed": phase in {"Succeeded", "Completed"},
            "Failed": phase == "Failed",
        }
        if kind in {ResourceKind.AGENT.value, ResourceKind.FLEET.value}:
            condition_values["Scheduled"] = phase in {"Pending", "Running", "Succeeded", "Failed"}

        by_type = {str(condition.get("type")): dict(condition) for condition in existing_conditions}
        for condition_type, is_true in condition_values.items():
            by_type[condition_type] = {
                "type": condition_type,
                "status": "True" if is_true else "False",
                "reason": reason,
                "message": message,
            }
        ordered_types = [
            "Scheduled",
            "Reconciling",
            "Running",
            "Completed",
            "Failed",
        ]
        ordered = [by_type.pop(condition_type) for condition_type in ordered_types if condition_type in by_type]
        ordered.extend(by_type.values())
        return ordered
