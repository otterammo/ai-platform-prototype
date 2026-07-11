from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeAlias, TypeVar

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
    CLUSTER_SCOPED_KINDS,
    AgentResource,
    AgentRunResource,
    AnyResource,
    ArtifactResource,
    ContextResource,
    FleetResource,
    KnowledgeIndexResource,
    KnowledgeResource,
    MissionResource,
    Observation,
    OwnerReference,
    ResourceKind,
    ToolInvocationResource,
    dump_resource,
    parse_resource,
    resource_key,
)

DEFAULT_DB_URL = "sqlite:///./platform.db"
CONTROLLER_FIELD_MANAGER = "controller"

LEGAL_OWNER_KINDS: dict[ResourceKind, set[ResourceKind]] = {
    ResourceKind.WORKSPACE: {ResourceKind.PLATFORM},
    ResourceKind.MISSION: {ResourceKind.WORKSPACE},
    ResourceKind.FLEET: {ResourceKind.MISSION},
    ResourceKind.AGENT: {ResourceKind.FLEET},
    ResourceKind.AGENT_RUN: {ResourceKind.AGENT},
    ResourceKind.TOOL_INVOCATION: {ResourceKind.AGENT_RUN},
    ResourceKind.CONTEXT: {ResourceKind.AGENT_RUN},
    ResourceKind.ARTIFACT: {ResourceKind.AGENT_RUN},
    ResourceKind.KNOWLEDGE: {ResourceKind.WORKSPACE},
    ResourceKind.KNOWLEDGE_INDEX: {ResourceKind.WORKSPACE},
}


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


class KnowledgeChunkRecord(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (UniqueConstraint("namespace", "index_name", "chunk_id", name="uq_knowledge_chunk_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    namespace: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    index_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source_ref: Mapped[str] = mapped_column(String(2000), nullable=False)
    document: Mapped[str] = mapped_column(String(2000), nullable=False, index=True)
    source_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    section: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    chunk_id: Mapped[str] = mapped_column(String(255), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content: Mapped[str] = mapped_column(String, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    chunk_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


JsonDict: TypeAlias = dict[str, Any]
JsonDictList: TypeAlias = list[JsonDict]
ResourceRecordList: TypeAlias = list[ResourceRecord]
_ResourceT = TypeVar("_ResourceT")


def make_engine(database_url: str = DEFAULT_DB_URL) -> Engine:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args)


def _is_default_status(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, dict):
        return False
    allowed_keys = {"phase", "observedGeneration", "message", "observation", "conditions", "data"}
    if any(key not in allowed_keys for key in value):
        return False
    return (
        value.get("phase", "Pending") == "Pending"
        and value.get("observedGeneration", 0) == 0
        and value.get("message") is None
        and value.get("observation") is None
        and value.get("conditions", []) == []
        and value.get("data", {}) == {}
    )


class ResourceStore:
    def __init__(self, database_url: str = DEFAULT_DB_URL, platform_root: str | Path = ".platform") -> None:
        self.database_url = database_url
        self.platform_root = Path(platform_root).expanduser().resolve()
        self.engine = make_engine(database_url)
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)
        self._ensure_platform()

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

    def apply(
        self,
        manifest: JsonDict,
        event_context: EventContext | None = None,
        *,
        field_manager: str = "user",
    ) -> JsonDict:
        if field_manager == "user" and "status" in manifest and not _is_default_status(manifest.get("status")):
            raise ValueError("status is controller-owned and cannot be set during apply")
        resource = parse_resource(manifest)
        self._default_owner_references(resource)
        self._admit(resource)
        kind, namespace, name = resource_key(resource.kind, resource.metadata.name, resource.metadata.namespace)
        correlation_id = event_context.correlation_id if event_context else None
        if kind == ResourceKind.MISSION.value:
            correlation_id = new_correlation_id()
        elif isinstance(resource, ToolInvocationResource):
            correlation_id = (
                correlation_id
                or resource.spec.correlationId
                or self._agent_run_correlation_id(resource.spec.agentRunRef.name, namespace)
                or new_correlation_id()
            )
        elif correlation_id is None:
            correlation_id = correlation_id_from_manifest(manifest)
        if correlation_id:
            resource.metadata.annotations[CORRELATION_ID_ANNOTATION] = correlation_id
            resource.status.data[CORRELATION_ID_STATUS_KEY] = correlation_id
        resource_event_payload = self._resource_event_payload(resource)
        with self.session() as session:
            record = session.scalar(
                select(ResourceRecord).where(
                    ResourceRecord.kind == kind,
                    ResourceRecord.namespace == namespace,
                    ResourceRecord.name == name,
                )
            )
            event_type = f"{kind}Created"
            previous_manifest: JsonDict | None = None
            if record:
                previous_manifest = dict(record.manifest)
                if isinstance(resource, ToolInvocationResource):
                    self._ensure_tool_invocation_spec_immutable(previous_manifest, dump_resource(resource))
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
                {
                    "generation": resource.metadata.generation,
                    "resourceSnapshot": dump_resource(resource),
                    **resource_event_payload,
                    **({"previousResourceSnapshot": previous_manifest} if previous_manifest else {}),
                },
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

    def _ensure_platform(self) -> None:
        with self.session() as session:
            existing = session.scalar(
                select(ResourceRecord).where(
                    ResourceRecord.kind == ResourceKind.PLATFORM.value,
                    ResourceRecord.namespace == "",
                    ResourceRecord.name == "local",
                )
            )
            if existing:
                return
            manifest = {
                "apiVersion": "ai.platform/v1",
                "kind": "Platform",
                "metadata": {"name": "local"},
                "spec": {"mode": "local"},
                "status": {"phase": "Ready", "observedGeneration": 1},
            }
            session.add(
                ResourceRecord(
                    api_version="ai.platform/v1",
                    kind=ResourceKind.PLATFORM.value,
                    namespace="",
                    name="local",
                    generation=1,
                    manifest=manifest,
                )
            )

    def _default_owner_references(self, resource: AnyResource) -> None:
        if resource.metadata.ownerReferences:
            return
        owner: OwnerReference | None = None
        namespace = resource.metadata.namespace
        if resource.kind == ResourceKind.WORKSPACE:
            owner = OwnerReference(kind=ResourceKind.PLATFORM, name="local", controller=True)
        elif isinstance(resource, MissionResource | KnowledgeResource | KnowledgeIndexResource):
            owner = OwnerReference(kind=ResourceKind.WORKSPACE, name=namespace or "", controller=True)
        elif isinstance(resource, ContextResource) and resource.spec.agentRun:
            owner = OwnerReference(kind=ResourceKind.AGENT_RUN, name=resource.spec.agentRun, controller=True)
        elif isinstance(resource, FleetResource):
            owner = OwnerReference(kind=ResourceKind.MISSION, name=resource.spec.mission, controller=True)
        elif isinstance(resource, AgentResource):
            owner = OwnerReference(kind=ResourceKind.FLEET, name=resource.spec.fleet, controller=True)
        elif isinstance(resource, AgentRunResource):
            owner = OwnerReference(kind=ResourceKind.AGENT, name=resource.spec.agentRef.name, controller=True)
        elif isinstance(resource, ToolInvocationResource):
            owner = OwnerReference(kind=ResourceKind.AGENT_RUN, name=resource.spec.agentRunRef.name, controller=True)
        elif isinstance(resource, ArtifactResource):
            owner = OwnerReference(kind=ResourceKind.AGENT_RUN, name=resource.spec.producedBy.name, controller=True)
        if owner is not None:
            resource.metadata.ownerReferences = [owner]

    def _admit(self, resource: AnyResource) -> None:
        namespace = resource.metadata.namespace
        if resource.kind == ResourceKind.PLATFORM:
            self._validate_owner_references(resource)
            return

        if resource.kind == ResourceKind.WORKSPACE:
            self._require_exists(ResourceKind.PLATFORM, "local", None)
            self._validate_owner_references(resource)
            return

        if resource.kind.value not in CLUSTER_SCOPED_KINDS:
            if not namespace:
                raise ValueError(f"{resource.kind.value} must name a workspace")
            self._require_exists(ResourceKind.WORKSPACE, namespace, None)

        if isinstance(resource, FleetResource):
            self._require_exists(ResourceKind.MISSION, resource.spec.mission, namespace)
        elif isinstance(resource, AgentResource):
            self._require_exists(ResourceKind.MISSION, resource.spec.mission, namespace)
            self._require_exists(ResourceKind.FLEET, resource.spec.fleet, namespace)
        elif isinstance(resource, AgentRunResource):
            self._require_exists(ResourceKind.AGENT, resource.spec.agentRef.name, namespace)
            self._require_exists(ResourceKind.MISSION, resource.spec.missionRef.name, namespace)
        elif isinstance(resource, ToolInvocationResource):
            self._require_exists(ResourceKind.AGENT_RUN, resource.spec.agentRunRef.name, namespace)
            self._require_exists(ResourceKind.TOOL, resource.spec.tool, None)
        elif isinstance(resource, ArtifactResource):
            self._require_exists(ResourceKind.AGENT_RUN, resource.spec.producedBy.name, namespace)
            artifact_path = Path(resource.spec.path)
            if artifact_path.is_absolute() or any(part in {"", ".", ".."} for part in artifact_path.parts):
                raise ValueError("Artifact spec.path must be a normalized workspace-relative path")
        elif isinstance(resource, ContextResource):
            self._require_exists(ResourceKind.MISSION, resource.spec.mission, namespace)
            if resource.spec.agentRun:
                self._require_exists(ResourceKind.AGENT_RUN, resource.spec.agentRun, namespace)

        if resource.kind == ResourceKind.APPROVAL:
            agent_run = getattr(resource.spec, "agentRun", None)
            if agent_run:
                self._require_exists(ResourceKind.AGENT_RUN, agent_run, resource.spec.workspace)

        self._validate_owner_references(resource)

    def _validate_owner_references(self, resource: AnyResource) -> None:
        self._validate_legal_owner_reference(resource)
        namespace = resource.metadata.namespace
        for owner in resource.metadata.ownerReferences:
            owner_kind = ResourceKind(owner.kind)
            owner_namespace = self._owner_namespace(namespace, owner_kind)
            self._require_exists(owner.kind, owner.name, owner_namespace)
        self._validate_owner_matches_spec(resource)
        self._validate_ownership_acyclic(resource)

    def _validate_legal_owner_reference(self, resource: AnyResource) -> None:
        allowed = LEGAL_OWNER_KINDS.get(resource.kind)
        owners = resource.metadata.ownerReferences
        if allowed is None:
            if owners:
                raise ValueError(f"{resource.kind.value} cannot set ownerReferences")
            return
        if len(owners) != 1 or not owners[0].controller:
            raise ValueError(f"{resource.kind.value} requires exactly one controller ownerReference")
        if owners[0].kind not in allowed:
            allowed_names = ", ".join(sorted(kind.value for kind in allowed))
            raise ValueError(f"{resource.kind.value} ownerReference must be one of: {allowed_names}")

    def _validate_owner_matches_spec(self, resource: AnyResource) -> None:
        if not resource.metadata.ownerReferences:
            return
        owner = resource.metadata.ownerReferences[0]
        namespace = resource.metadata.namespace
        if resource.kind == ResourceKind.WORKSPACE:
            if owner.kind != ResourceKind.PLATFORM or owner.name != "local":
                raise ValueError("Workspace ownerReference must be Platform local")
        elif isinstance(resource, MissionResource | KnowledgeResource | KnowledgeIndexResource):
            if owner.kind != ResourceKind.WORKSPACE or owner.name != namespace:
                raise ValueError(f"{resource.kind.value} ownerReference must match metadata.namespace")
        elif isinstance(resource, FleetResource):
            if owner.kind != ResourceKind.MISSION or owner.name != resource.spec.mission:
                raise ValueError("Fleet ownerReference must match spec.mission")
        elif isinstance(resource, AgentResource):
            if owner.kind != ResourceKind.FLEET or owner.name != resource.spec.fleet:
                raise ValueError("Agent ownerReference must match spec.fleet")
            fleet = self._load_resource(ResourceKind.FLEET, resource.spec.fleet, namespace, FleetResource)
            if fleet.spec.mission != resource.spec.mission or fleet.spec.workspace != resource.spec.workspace:
                raise ValueError("Agent spec must match owning Fleet workspace and mission")
        elif isinstance(resource, AgentRunResource):
            if owner.kind != ResourceKind.AGENT or owner.name != resource.spec.agentRef.name:
                raise ValueError("AgentRun ownerReference must match spec.agentRef.name")
            agent = self._load_resource(ResourceKind.AGENT, resource.spec.agentRef.name, namespace, AgentResource)
            if agent.spec.mission != resource.spec.missionRef.name:
                raise ValueError("AgentRun spec.missionRef must match owning Agent mission")
        elif isinstance(resource, ToolInvocationResource):
            if owner.kind != ResourceKind.AGENT_RUN or owner.name != resource.spec.agentRunRef.name:
                raise ValueError("ToolInvocation ownerReference must match spec.agentRunRef.name")
        elif isinstance(resource, ContextResource):
            if owner.kind != ResourceKind.AGENT_RUN:
                raise ValueError("Context ownerReference must be AgentRun")
            if resource.spec.agentRun and resource.spec.agentRun != owner.name:
                raise ValueError("Context spec.agentRun must match ownerReference")
            run = self._load_resource(ResourceKind.AGENT_RUN, owner.name, namespace, AgentRunResource)
            if run.spec.contextRef.name != resource.metadata.name:
                raise ValueError("Context metadata.name must match owning AgentRun spec.contextRef")
            if run.spec.missionRef.name != resource.spec.mission:
                raise ValueError("Context spec.mission must match owning AgentRun missionRef")
        elif isinstance(resource, ArtifactResource):
            if owner.kind != ResourceKind.AGENT_RUN or owner.name != resource.spec.producedBy.name:
                raise ValueError("Artifact ownerReference must match spec.producedBy.name")

    def _load_resource(
        self,
        kind: ResourceKind,
        name: str,
        namespace: str | None,
        expected_type: type[_ResourceT],
    ) -> _ResourceT:
        manifest = self.get(kind, name, namespace)
        if manifest is None:
            display = self.display_name(namespace, name)
            raise ValueError(f"{kind.value} {display} does not exist")
        resource = parse_resource(manifest)
        if not isinstance(resource, expected_type):
            raise TypeError(f"expected {expected_type.__name__}, got {type(resource).__name__}")
        return resource

    def _validate_ownership_acyclic(self, resource: AnyResource) -> None:
        kind_value, namespace_value, name_value = resource_key(
            resource.kind,
            resource.metadata.name,
            resource.metadata.namespace,
        )
        target_key = (kind_value, namespace_value, name_value)
        seen: set[tuple[str, str, str]] = set()
        stack = [
            (
                ResourceKind(owner.kind).value,
                self._owner_namespace(resource.metadata.namespace, ResourceKind(owner.kind)) or "",
                owner.name,
            )
            for owner in resource.metadata.ownerReferences
        ]
        while stack:
            current = stack.pop()
            if current == target_key:
                raise ValueError("ownerReferences must not create an ownership cycle")
            if current in seen:
                continue
            seen.add(current)
            owner_kind, owner_namespace, owner_name = current
            manifest = self.get(owner_kind, owner_name, owner_namespace or None)
            if manifest is None:
                continue
            owner_metadata = manifest.get("metadata") or {}
            owner_resource = parse_resource(manifest)
            for next_owner in owner_metadata.get("ownerReferences") or []:
                next_kind = ResourceKind(next_owner.get("kind"))
                stack.append(
                    (
                        next_kind.value,
                        self._owner_namespace(owner_resource.metadata.namespace, next_kind) or "",
                        str(next_owner.get("name")),
                    )
                )

    @staticmethod
    def _owner_namespace(child_namespace: str | None, owner_kind: ResourceKind) -> str | None:
        if owner_kind == ResourceKind.WORKSPACE or owner_kind.value in CLUSTER_SCOPED_KINDS:
            return None
        if child_namespace is None:
            raise ValueError(f"cannot reference namespaced owner {owner_kind.value} from a cluster-scoped resource")
        return child_namespace

    def _require_exists(self, kind: str | ResourceKind, name: str | None, namespace: str | None) -> None:
        if not name:
            raise ValueError(f"{ResourceKind(kind).value} reference must include a name")
        if self.get(kind, name, namespace) is None:
            display = self.display_name(namespace, name)
            raise ValueError(f"{ResourceKind(kind).value} {display} does not exist")

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
            elif kind_value == ResourceKind.KNOWLEDGE_INDEX.value:
                session.execute(
                    sql_delete(KnowledgeChunkRecord).where(
                        KnowledgeChunkRecord.namespace == namespace_value,
                        KnowledgeChunkRecord.index_name == name_value,
                    )
                )
                self._delete_record(session, record, deleted_ids)
            else:
                self._delete_record(session, record, deleted_ids)
            return True

    def _delete_workspace(self, session: Session, record: ResourceRecord, deleted_ids: set[int]) -> None:
        workspace_name = record.name
        children = session.scalars(select(ResourceRecord).where(ResourceRecord.namespace == workspace_name)).all()
        delete_order = {
            ResourceKind.ARTIFACT.value: 0,
            ResourceKind.AGENT_RUN.value: 1,
            ResourceKind.CONTEXT.value: 2,
            ResourceKind.AGENT.value: 3,
            ResourceKind.FLEET.value: 4,
            ResourceKind.MISSION.value: 5,
            ResourceKind.KNOWLEDGE_INDEX.value: 6,
            ResourceKind.KNOWLEDGE.value: 7,
        }
        for child in sorted(children, key=lambda item: delete_order.get(item.kind, 99)):
            self._delete_record(session, child, deleted_ids, cascade="workspace")
        session.execute(sql_delete(ArtifactRecord).where(ArtifactRecord.namespace == workspace_name))
        session.execute(sql_delete(KnowledgeChunkRecord).where(KnowledgeChunkRecord.namespace == workspace_name))
        self._delete_record(session, record, deleted_ids)

    def _delete_mission(self, session: Session, record: ResourceRecord, deleted_ids: set[int]) -> None:
        namespace = record.namespace
        mission_name = record.name
        self._delete_owned_children(session, record, deleted_ids, cascade="mission")
        agents = self._owned_agents_for_mission(session, namespace, mission_name)
        fleets = self._owned_fleets_for_mission(session, namespace, mission_name)
        for agent in agents:
            self._delete_record(session, agent, deleted_ids, cascade="mission")
        for fleet in fleets:
            self._delete_record(session, fleet, deleted_ids, cascade="mission")
        context = session.scalar(
            select(ResourceRecord).where(
                ResourceRecord.kind == ResourceKind.CONTEXT.value,
                ResourceRecord.namespace == namespace,
                ResourceRecord.name == mission_name,
            )
        )
        if context:
            self._delete_record(session, context, deleted_ids, cascade="mission")
        session.execute(
            sql_delete(ArtifactRecord).where(
                ArtifactRecord.namespace == namespace,
                ArtifactRecord.mission == mission_name,
            )
        )
        self._delete_record(session, record, deleted_ids)

    def _delete_fleet(self, session: Session, record: ResourceRecord, deleted_ids: set[int]) -> None:
        self._delete_owned_children(session, record, deleted_ids, cascade="fleet")
        for agent in self._owned_agents_for_fleet(session, record.namespace, record.name):
            self._delete_record(session, agent, deleted_ids, cascade="fleet")
        self._delete_record(session, record, deleted_ids)

    def _delete_owned_children(
        self,
        session: Session,
        record: ResourceRecord,
        deleted_ids: set[int],
        cascade: str,
    ) -> None:
        records = session.scalars(select(ResourceRecord)).all()
        for candidate in records:
            if candidate.id == record.id:
                continue
            manifest = candidate.manifest
            metadata = manifest.get("metadata") or {}
            owners = metadata.get("ownerReferences") or []
            for owner in owners:
                if owner.get("kind") == record.kind and owner.get("name") == record.name:
                    if record.kind not in CLUSTER_SCOPED_KINDS and candidate.namespace != record.namespace:
                        continue
                    self._delete_owned_children(session, candidate, deleted_ids, cascade)
                    self._delete_record(session, candidate, deleted_ids, cascade=cascade)
                    break

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
        payload["resourceSnapshot"] = record.manifest
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
        clear_data_keys: Sequence[str] | None = None,
        observation: Observation | dict[str, Any] | None = None,
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
            if data or correlation_id or clear_data_keys:
                merged = dict(existing_data)
                for key in clear_data_keys or []:
                    merged.pop(key, None)
                if correlation_id:
                    merged[CORRELATION_ID_STATUS_KEY] = correlation_id
                if data:
                    merged.update(data)
                status["data"] = merged
            if observation is not None:
                if isinstance(observation, Observation):
                    status["observation"] = observation.model_dump(
                        mode="json",
                        exclude_none=True,
                        exclude_defaults=True,
                    )
                else:
                    status["observation"] = observation
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
                status_event_payload = {
                    "phase": phase,
                    "data": data or {},
                    "resourceSnapshot": manifest,
                }
                if data:
                    status_event_payload.update(data)
                event_payload = self._structured_payload(
                    status_event_payload,
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
            statement = select(ResourceRecord).where(ResourceRecord.kind == ResourceKind.ARTIFACT.value)
            if namespace is not None:
                statement = statement.where(ResourceRecord.namespace == namespace)
            statement = statement.order_by(ResourceRecord.updated_at.desc(), ResourceRecord.id.desc())
            artifacts = []
            for record in session.scalars(statement).all():
                manifest = record.manifest
                status = manifest.get("status") or {}
                spec = manifest.get("spec") or {}
                artifact_mission = status.get("data", {}).get("mission")
                if mission is not None and artifact_mission != mission:
                    continue
                artifacts.append(
                    {
                        "id": record.id,
                        "namespace": record.namespace,
                        "mission": artifact_mission,
                        "agent": status.get("data", {}).get("agent"),
                        "agentRun": status.get("data", {}).get("agentRun") or spec.get("producedBy", {}).get("name"),
                        "name": record.name,
                        "path": (
                            status.get("data", {}).get("absolutePath")
                            or status.get("data", {}).get("path")
                            or spec.get("path")
                        ),
                        "createdAt": record.created_at.isoformat(),
                    }
                )
            return artifacts

    def replace_knowledge_chunks(
        self,
        namespace: str,
        index_name: str,
        chunks: JsonDictList,
    ) -> JsonDictList:
        with self.session() as session:
            session.execute(
                sql_delete(KnowledgeChunkRecord).where(
                    KnowledgeChunkRecord.namespace == namespace,
                    KnowledgeChunkRecord.index_name == index_name,
                )
            )
            records = []
            for chunk in chunks:
                record = KnowledgeChunkRecord(
                    namespace=namespace,
                    index_name=index_name,
                    source_ref=str(chunk["sourceRef"]),
                    document=str(chunk["document"]),
                    source_order=int(chunk.get("sourceOrder", 0)),
                    source_hash=str(chunk["sourceHash"]),
                    section=str(chunk.get("section") or ""),
                    chunk_id=str(chunk["chunkId"]),
                    chunk_index=int(chunk.get("chunkIndex", 0)),
                    content=str(chunk["content"]),
                    content_hash=str(chunk["contentHash"]),
                    chunk_metadata=dict(chunk.get("metadata") or {}),
                )
                session.add(record)
                records.append(record)
            session.flush()
            return [self.knowledge_chunk_to_dict(record) for record in records]

    def list_knowledge_chunks(self, namespace: str, index_name: str) -> JsonDictList:
        with self.session() as session:
            records = session.scalars(
                select(KnowledgeChunkRecord)
                .where(
                    KnowledgeChunkRecord.namespace == namespace,
                    KnowledgeChunkRecord.index_name == index_name,
                )
                .order_by(KnowledgeChunkRecord.source_order, KnowledgeChunkRecord.chunk_index, KnowledgeChunkRecord.id)
            ).all()
            return [self.knowledge_chunk_to_dict(record) for record in records]

    def delete_knowledge_chunks(self, namespace: str, index_name: str | None = None) -> None:
        with self.session() as session:
            statement = sql_delete(KnowledgeChunkRecord).where(KnowledgeChunkRecord.namespace == namespace)
            if index_name is not None:
                statement = statement.where(KnowledgeChunkRecord.index_name == index_name)
            session.execute(statement)

    @staticmethod
    def knowledge_chunk_to_dict(record: KnowledgeChunkRecord) -> JsonDict:
        return {
            "id": record.id,
            "namespace": record.namespace,
            "index": record.index_name,
            "sourceRef": record.source_ref,
            "document": record.document,
            "sourceOrder": record.source_order,
            "sourceHash": record.source_hash,
            "section": record.section,
            "chunkId": record.chunk_id,
            "chunkIndex": record.chunk_index,
            "content": record.content,
            "contentHash": record.content_hash,
            "metadata": record.chunk_metadata,
            "createdAt": record.created_at.isoformat(),
        }

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
    def _ensure_tool_invocation_spec_immutable(previous_manifest: JsonDict, next_manifest: JsonDict) -> None:
        if (previous_manifest.get("spec") or {}) != (next_manifest.get("spec") or {}):
            metadata = previous_manifest.get("metadata") or {}
            name = metadata.get("name") or "<unknown>"
            namespace = metadata.get("namespace")
            display_name = ResourceStore.display_name(namespace, str(name))
            raise ValueError(f"ToolInvocation {display_name} spec is immutable")

    def _agent_run_correlation_id(self, agent_run_name: str, namespace: str | None) -> str | None:
        manifest = self.get(ResourceKind.AGENT_RUN, agent_run_name, namespace)
        if manifest is None:
            return None
        return correlation_id_from_manifest(manifest)

    @staticmethod
    def _resource_event_payload(resource: AnyResource) -> dict[str, Any]:
        if isinstance(resource, ToolInvocationResource):
            workspace = resource.metadata.namespace
            return {
                "workspace": workspace,
                "agentRun": resource.spec.agentRunRef.name,
                "toolInvocation": resource.metadata.name,
                "tool": resource.spec.tool,
                "operation": resource.spec.operation,
            }
        return {}

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
            "WaitingForApproval": phase == "Waiting",
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
            "WaitingForApproval",
            "Reconciling",
            "Running",
            "Completed",
            "Failed",
        ]
        ordered = [by_type.pop(condition_type) for condition_type in ordered_types if condition_type in by_type]
        ordered.extend(by_type.values())
        return ordered
