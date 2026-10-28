import { FormEvent, KeyboardEvent as ReactKeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  AudioLines,
  BookOpen,
  Check,
  FileImage,
  FileText,
  Image,
  Layers3,
  Link2,
  Loader2,
  Pencil,
  Plus,
  Search,
  Settings2,
  Trash2,
  Video,
  X,
} from "lucide-react";
import type { Asset } from "./types";
import "./ScreenplayWorkspace.css";

type ProjectStatus = "draft" | "in_progress" | "completed";

type ScreenplayProjectSummary = {
  id: string;
  title: string;
  logline: string;
  status: ProjectStatus;
  scene_count: number;
  created_at: string;
  updated_at: string;
};

type ScreenplayScene = {
  id: string;
  position: number;
  heading: string;
  content: string;
  notes: string;
  assets: Asset[];
  created_at: string;
  updated_at: string;
};

type ScreenplayProject = ScreenplayProjectSummary & {
  scenes: ScreenplayScene[];
};

type ProjectFormValues = {
  title: string;
  logline: string;
  status: ProjectStatus;
};

type SaveState = "idle" | "pending" | "saving" | "saved" | "error";

const statusLabels: Record<ProjectStatus, string> = {
  draft: "草稿",
  in_progress: "创作中",
  completed: "已完成",
};

function formatShortDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric" }).format(new Date(value));
}

function assetTypeLabel(type: string) {
  if (type === "image") return "图片";
  if (type === "video") return "视频";
  if (type === "audio") return "音频";
  if (type === "text" || type === "application") return "文档";
  return "素材";
}

function AssetTypeIcon({ type }: { type: string }) {
  if (type === "image") return <Image size={15} />;
  if (type === "video") return <Video size={15} />;
  if (type === "audio") return <AudioLines size={15} />;
  if (type === "text" || type === "application") return <FileText size={15} />;
  return <FileImage size={15} />;
}

async function parseError(response: Response) {
  try {
    const body = await response.json();
    return body.detail || "请求失败 (" + response.status + ")";
  } catch {
    return "请求失败 (" + response.status + ")";
  }
}

function ProjectDialog({
  mode,
  project,
  busy,
  error,
  onClose,
  onSubmit,
  onDelete,
}: {
  mode: "create" | "edit";
  project?: ScreenplayProject;
  busy: boolean;
  error: string;
  onClose: () => void;
  onSubmit: (values: ProjectFormValues) => void;
  onDelete?: () => void;
}) {
  const [title, setTitle] = useState(project?.title || "");
  const [logline, setLogline] = useState(project?.logline || "");
  const [status, setStatus] = useState<ProjectStatus>(project?.status || "draft");

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) onClose();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [busy, onClose]);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!title.trim() || busy) return;
    onSubmit({ title: title.trim(), logline: logline.trim(), status });
  }

  return (
    <div className="modal-backdrop screenplay-modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && !busy && onClose()}>
      <section className="screenplay-dialog" role="dialog" aria-modal="true" aria-labelledby="screenplay-dialog-title">
        <header className="dialog-head">
          <div>
            <span className="screenplay-dialog-kicker">SCREENPLAY PROJECT</span>
            <h2 id="screenplay-dialog-title">{mode === "create" ? "新建剧本项目" : "项目设置"}</h2>
            <p>{mode === "create" ? "先建立创作空间，场景可以随时继续补充" : "修改项目名称、故事梗概和创作状态"}</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} disabled={busy} aria-label="关闭">
            <X size={18} />
          </button>
        </header>
        <form onSubmit={submit}>
          <div className="screenplay-dialog-fields">
            <label>
              项目名称
              <input
                autoFocus
                value={title}
                maxLength={200}
                onChange={(event) => setTitle(event.target.value)}
                placeholder="例如：夜航"
                required
              />
            </label>
            <label>
              故事梗概
              <textarea
                value={logline}
                maxLength={2000}
                onChange={(event) => setLogline(event.target.value)}
                placeholder="用一两句话记录故事核心、人物和冲突"
              />
            </label>
            <label>
              创作状态
              <select value={status} onChange={(event) => setStatus(event.target.value as ProjectStatus)}>
                <option value="draft">草稿</option>
                <option value="in_progress">创作中</option>
                <option value="completed">已完成</option>
              </select>
            </label>
          </div>
          {error && <div className="form-error" role="alert">{error}</div>}
          <footer className="dialog-actions screenplay-dialog-actions">
            {mode === "edit" && onDelete && (
              <button type="button" className="screenplay-danger-link" onClick={onDelete} disabled={busy}>
                <Trash2 size={14} />删除项目
              </button>
            )}
            <span className="dialog-action-spacer" />
            <button type="button" className="secondary-button" onClick={onClose} disabled={busy}>取消</button>
            <button className="primary-button" disabled={busy || !title.trim()}>
              {busy ? <Loader2 className="spin-icon" size={15} /> : mode === "create" ? <Plus size={15} /> : <Check size={15} />}
              {busy ? "正在保存…" : mode === "create" ? "创建项目" : "保存设置"}
            </button>
          </footer>
        </form>
      </section>
    </div>
  );
}

function SaveIndicator({ state, error, onRetry }: { state: SaveState; error: string; onRetry: () => void }) {
  if (state === "error") {
    return (
      <span className="save-indicator is-error" role="alert">
        <span>{error || "自动保存失败"}</span>
        <button type="button" onClick={onRetry}>重试</button>
      </span>
    );
  }
  if (state === "saving") return <span className="save-indicator"><Loader2 className="spin-icon" size={12} />正在保存</span>;
  if (state === "pending") return <span className="save-indicator"><span className="save-dot" />等待保存</span>;
  if (state === "saved") return <span className="save-indicator is-saved"><Check size={12} />已自动保存</span>;
  return <span className="save-indicator"><Check size={12} />自动保存已开启</span>;
}

export default function ScreenplayWorkspace({
  csrf,
  active,
  onUnauthorized,
  onOpenImage,
  onOpenVideo,
  onProjectCountChange,
}: {
  csrf: string;
  active: boolean;
  onUnauthorized: () => void;
  onOpenImage: (asset: Asset) => void;
  onOpenVideo: (asset: Asset) => void;
  onProjectCountChange: (count: number) => void;
}) {
  const [projects, setProjects] = useState<ScreenplayProjectSummary[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [project, setProject] = useState<ScreenplayProject | null>(null);
  const [selectedSceneId, setSelectedSceneId] = useState<string | null>(null);
  const [projectSearch, setProjectSearch] = useState("");
  const [projectsLoading, setProjectsLoading] = useState(true);
  const [projectLoading, setProjectLoading] = useState(false);
  const [workspaceError, setWorkspaceError] = useState("");
  const [sceneBusy, setSceneBusy] = useState(false);

  const [libraryAssets, setLibraryAssets] = useState<Asset[]>([]);
  const [assetSearch, setAssetSearch] = useState("");
  const [assetTab, setAssetTab] = useState<"linked" | "library">("linked");
  const [busyAssetId, setBusyAssetId] = useState<string | null>(null);
  const [assetsPanelOpen, setAssetsPanelOpen] = useState(false);

  const [dialogMode, setDialogMode] = useState<"create" | "edit" | null>(null);
  const [dialogBusy, setDialogBusy] = useState(false);
  const [dialogError, setDialogError] = useState("");

  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [saveError, setSaveError] = useState("");
  const [saveTick, setSaveTick] = useState(0);
  const dirtyScenesRef = useRef<Record<string, number>>({});
  const sceneVersionsRef = useRef<Record<string, number>>({});
  const savingPromiseRef = useRef<Promise<boolean> | null>(null);
  const projectRef = useRef<ScreenplayProject | null>(null);
  const selectedProjectIdRef = useRef<string | null>(null);
  const unauthorizedRef = useRef(onUnauthorized);
  const projectCountRef = useRef(onProjectCountChange);

  unauthorizedRef.current = onUnauthorized;
  projectCountRef.current = onProjectCountChange;

  useEffect(() => {
    projectRef.current = project;
  }, [project]);

  useEffect(() => {
    selectedProjectIdRef.current = selectedProjectId;
  }, [selectedProjectId]);

  useEffect(() => {
    projectCountRef.current(projects.length);
  }, [projects.length]);

  useEffect(() => {
    let cancelled = false;
    setProjectsLoading(true);
    fetch("/api/screenplays")
      .then(async (response) => {
        if (response.status === 401) {
          unauthorizedRef.current();
          return [];
        }
        if (!response.ok) throw new Error(await parseError(response));
        return (await response.json()) as ScreenplayProjectSummary[];
      })
      .then((items) => {
        if (!cancelled) setProjects(items);
      })
      .catch((error) => {
        if (!cancelled) setWorkspaceError(error instanceof Error ? error.message : "剧本项目加载失败");
      })
      .finally(() => {
        if (!cancelled) setProjectsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [csrf]);

  useEffect(() => {
    if (!selectedProjectId && projects.length > 0) {
      setSelectedProjectId(projects[0].id);
    }
  }, [projects, selectedProjectId]);

  useEffect(() => {
    if (!selectedProjectId) {
      setProject(null);
      return;
    }
    let cancelled = false;
    setProjectLoading(true);
    setWorkspaceError("");
    fetch("/api/screenplays/" + selectedProjectId)
      .then(async (response) => {
        if (response.status === 401) {
          unauthorizedRef.current();
          throw new Error("登录已失效");
        }
        if (!response.ok) throw new Error(await parseError(response));
        return (await response.json()) as ScreenplayProject;
      })
      .then((item) => {
        if (cancelled) return;
        dirtyScenesRef.current = {};
        sceneVersionsRef.current = {};
        setProject(item);
        setSelectedSceneId((current) => item.scenes.some((scene) => scene.id === current) ? current : item.scenes[0]?.id || null);
        setSaveState("idle");
        setSaveError("");
      })
      .catch((error) => {
        if (!cancelled && !(error instanceof Error && error.message === "登录已失效")) {
          setWorkspaceError(error instanceof Error ? error.message : "剧本加载失败");
        }
      })
      .finally(() => {
        if (!cancelled) setProjectLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedProjectId]);

  useEffect(() => {
    if (!active) return;
    fetch("/api/assets")
      .then(async (response) => {
        if (response.status === 401) {
          unauthorizedRef.current();
          return [];
        }
        if (!response.ok) throw new Error(await parseError(response));
        return (await response.json()) as Asset[];
      })
      .then((items) => setLibraryAssets(items))
      .catch((error) => setWorkspaceError(error instanceof Error ? error.message : "资产库加载失败"));
  }, [active, csrf]);

  useEffect(() => {
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      if (Object.keys(dirtyScenesRef.current).length > 0) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, []);

  async function persistScene(sceneId: string): Promise<boolean> {
    if (savingPromiseRef.current) return savingPromiseRef.current;
    const currentProject = projectRef.current;
    const scene = currentProject?.scenes.find((item) => item.id === sceneId);
    const version = dirtyScenesRef.current[sceneId];
    if (!currentProject || !scene || version === undefined) return true;

    setSaveState("saving");
    setSaveError("");
    const request = (async () => {
      try {
        const response = await fetch(`/api/screenplays/${currentProject.id}/scenes/${sceneId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
          body: JSON.stringify({
            heading: scene.heading,
            content: scene.content,
            notes: scene.notes,
          }),
        });
        if (response.status === 401) {
          unauthorizedRef.current();
          return false;
        }
        if (!response.ok) throw new Error(await parseError(response));
        const saved = (await response.json()) as ScreenplayScene;
        if ((sceneVersionsRef.current[sceneId] ?? 0) === version) {
          delete dirtyScenesRef.current[sceneId];
          setSaveState(Object.keys(dirtyScenesRef.current).length > 0 ? "pending" : "saved");
          setProject((current) => {
            if (!current || current.id !== currentProject.id) return current;
            return {
              ...current,
              updated_at: new Date().toISOString(),
              scenes: current.scenes.map((item) => item.id === sceneId ? { ...item, updated_at: saved.updated_at } : item),
            };
          });
        }
        return true;
      } catch (error) {
        setSaveError(error instanceof Error ? error.message : "自动保存失败");
        setSaveState("error");
        return false;
      } finally {
        savingPromiseRef.current = null;
      }
    })();
    savingPromiseRef.current = request;
    return request;
  }

  useEffect(() => {
    if (!project || savingPromiseRef.current) return;
    const nextSceneId = Object.keys(dirtyScenesRef.current)[0];
    if (!nextSceneId) return;
    const timer = window.setTimeout(() => {
      void persistScene(nextSceneId).then((saved) => {
        if (saved) setSaveTick((value) => value + 1);
      });
    }, 700);
    return () => window.clearTimeout(timer);
  }, [project?.id, saveTick]);

  async function flushDirtyScenes() {
    for (let attempt = 0; attempt < 50; attempt += 1) {
      if (savingPromiseRef.current) {
        const saved = await savingPromiseRef.current;
        if (!saved) return false;
        continue;
      }
      const sceneId = Object.keys(dirtyScenesRef.current)[0];
      if (!sceneId) return true;
      const saved = await persistScene(sceneId);
      if (!saved) return false;
      setSaveTick((value) => value + 1);
    }
    return Object.keys(dirtyScenesRef.current).length === 0;
  }

  async function loadProjectList() {
    try {
      const response = await fetch("/api/screenplays");
      if (response.status === 401) {
        unauthorizedRef.current();
        return;
      }
      if (!response.ok) throw new Error(await parseError(response));
      setProjects((await response.json()) as ScreenplayProjectSummary[]);
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : "项目列表刷新失败");
    }
  }

  async function selectProject(projectId: string) {
    if (projectId === selectedProjectIdRef.current) return;
    setWorkspaceError("");
    if (!(await flushDirtyScenes())) {
      setWorkspaceError("当前场景尚未保存，已停留在本项目中");
      return;
    }
    setSelectedProjectId(projectId);
  }

  async function openProjectDialog(mode: "create" | "edit") {
    if (!(await flushDirtyScenes())) {
      setWorkspaceError("当前场景尚未保存，请重试后再继续");
      return;
    }
    setDialogError("");
    setDialogMode(mode);
  }

  async function submitProjectDialog(values: ProjectFormValues) {
    setDialogBusy(true);
    setDialogError("");
    try {
      const editing = dialogMode === "edit" && project;
      const response = await fetch(editing ? "/api/screenplays/" + project.id : "/api/screenplays", {
        method: editing ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
        body: JSON.stringify(values),
      });
      if (response.status === 401) {
        unauthorizedRef.current();
        return;
      }
      if (!response.ok) throw new Error(await parseError(response));
      const saved = (await response.json()) as ScreenplayProject;
      projectRef.current = saved;
      setProject(saved);
      setSelectedSceneId(saved.scenes[0]?.id || null);
      setProjects((current) => {
        const summary: ScreenplayProjectSummary = {
          id: saved.id,
          title: saved.title,
          logline: saved.logline,
          status: saved.status,
          scene_count: saved.scene_count,
          created_at: saved.created_at,
          updated_at: saved.updated_at,
        };
        return [summary, ...current.filter((item) => item.id !== summary.id)];
      });
      setSelectedProjectId(saved.id);
      setDialogMode(null);
      setSaveState("idle");
    } catch (error) {
      setDialogError(error instanceof Error ? error.message : "项目保存失败");
    } finally {
      setDialogBusy(false);
    }
  }

  async function deleteProject() {
    if (!project || !window.confirm(`确定删除剧本「${project.title}」及其全部场景吗？此操作不可恢复。`)) return;
    setDialogBusy(true);
    setDialogError("");
    try {
      const response = await fetch("/api/screenplays/" + project.id, {
        method: "DELETE",
        headers: { "X-CSRF-Token": csrf },
      });
      if (response.status === 401) {
        unauthorizedRef.current();
        return;
      }
      if (!response.ok) throw new Error(await parseError(response));
      const remaining = projects.filter((item) => item.id !== project.id);
      setProjects(remaining);
      setProject(null);
      projectRef.current = null;
      dirtyScenesRef.current = {};
      sceneVersionsRef.current = {};
      setSelectedSceneId(null);
      setSelectedProjectId(remaining[0]?.id || null);
      setDialogMode(null);
      setSaveState("idle");
    } catch (error) {
      setDialogError(error instanceof Error ? error.message : "项目删除失败");
    } finally {
      setDialogBusy(false);
    }
  }

  function updateScene(field: "heading" | "content" | "notes", value: string) {
    if (!project || !selectedSceneId) return;
    const sceneId = selectedSceneId;
    const nextVersion = (sceneVersionsRef.current[sceneId] ?? 0) + 1;
    sceneVersionsRef.current[sceneId] = nextVersion;
    dirtyScenesRef.current[sceneId] = nextVersion;
    setProject((current) => {
      if (!current) return current;
      return {
        ...current,
        scenes: current.scenes.map((scene) => scene.id === sceneId ? { ...scene, [field]: value } : scene),
      };
    });
    setSaveError("");
    setSaveState("pending");
    setSaveTick((value) => value + 1);
  }

  function insertIndent(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Tab" || !project || !selectedSceneId) return;
    event.preventDefault();
    const target = event.currentTarget;
    const start = target.selectionStart;
    const end = target.selectionEnd;
    const value = target.value.slice(0, start) + "  " + target.value.slice(end);
    updateScene("content", value);
    window.requestAnimationFrame(() => {
      target.selectionStart = start + 2;
      target.selectionEnd = start + 2;
    });
  }

  async function addScene() {
    if (!project || sceneBusy) return;
    setSceneBusy(true);
    setWorkspaceError("");
    try {
      const response = await fetch(`/api/screenplays/${project.id}/scenes`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
        body: JSON.stringify({}),
      });
      if (response.status === 401) {
        unauthorizedRef.current();
        return;
      }
      if (!response.ok) throw new Error(await parseError(response));
      const scene = (await response.json()) as ScreenplayScene;
      setProject((current) => current ? {
        ...current,
        scene_count: current.scenes.length + 1,
        scenes: [...current.scenes, scene],
      } : current);
      setSelectedSceneId(scene.id);
      setSaveState("idle");
      void loadProjectList();
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : "场景创建失败");
    } finally {
      setSceneBusy(false);
    }
  }

  async function deleteScene() {
    if (!project || !selectedSceneId || project.scenes.length <= 1 || sceneBusy) return;
    const target = project.scenes.find((scene) => scene.id === selectedSceneId);
    if (!target || !window.confirm(`确定删除「${target.heading || "未命名场景"}」吗？`)) return;
    setSceneBusy(true);
    setWorkspaceError("");
    try {
      const response = await fetch(`/api/screenplays/${project.id}/scenes/${target.id}`, {
        method: "DELETE",
        headers: { "X-CSRF-Token": csrf },
      });
      if (response.status === 401) {
        unauthorizedRef.current();
        return;
      }
      if (!response.ok) throw new Error(await parseError(response));
      const currentIndex = project.scenes.findIndex((scene) => scene.id === target.id);
      const remaining = project.scenes
        .filter((scene) => scene.id !== target.id)
        .map((scene, position) => ({ ...scene, position }));
      delete dirtyScenesRef.current[target.id];
      delete sceneVersionsRef.current[target.id];
      setProject((current) => current ? { ...current, scene_count: remaining.length, scenes: remaining } : current);
      setSelectedSceneId(remaining[Math.min(currentIndex, remaining.length - 1)]?.id || null);
      setSaveState("idle");
      void loadProjectList();
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : "场景删除失败");
    } finally {
      setSceneBusy(false);
    }
  }

  async function moveScene(direction: -1 | 1) {
    if (!project || !selectedSceneId || sceneBusy) return;
    const index = project.scenes.findIndex((scene) => scene.id === selectedSceneId);
    const nextIndex = index + direction;
    if (index < 0 || nextIndex < 0 || nextIndex >= project.scenes.length) return;
    if (!(await flushDirtyScenes())) {
      setWorkspaceError("当前场景尚未保存，暂时无法调整顺序");
      return;
    }
    const reordered = [...project.scenes];
    [reordered[index], reordered[nextIndex]] = [reordered[nextIndex], reordered[index]];
    const withPositions = reordered.map((scene, position) => ({ ...scene, position }));
    setProject((current) => current ? { ...current, scenes: withPositions } : current);
    setSceneBusy(true);
    setWorkspaceError("");
    try {
      const response = await fetch(`/api/screenplays/${project.id}/scenes/reorder`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
        body: JSON.stringify({ scene_ids: withPositions.map((scene) => scene.id) }),
      });
      if (response.status === 401) {
        unauthorizedRef.current();
        return;
      }
      if (!response.ok) throw new Error(await parseError(response));
      setProject((current) => current ? { ...current, updated_at: new Date().toISOString() } : current);
      void loadProjectList();
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : "场景排序失败");
      setSelectedProjectId(null);
      window.setTimeout(() => setSelectedProjectId(project.id), 0);
    } finally {
      setSceneBusy(false);
    }
  }

  async function linkAsset(asset: Asset) {
    if (!project || !selectedSceneId || busyAssetId) return;
    setBusyAssetId(asset.id);
    setWorkspaceError("");
    try {
      const response = await fetch(`/api/screenplays/${project.id}/scenes/${selectedSceneId}/assets/${asset.id}`, {
        method: "POST",
        headers: { "X-CSRF-Token": csrf },
      });
      if (response.status === 401) {
        unauthorizedRef.current();
        return;
      }
      if (!response.ok) throw new Error(await parseError(response));
      const updated = (await response.json()) as ScreenplayScene;
      setProject((current) => current ? {
        ...current,
        scenes: current.scenes.map((scene) => scene.id === updated.id ? { ...scene, assets: updated.assets, updated_at: updated.updated_at } : scene),
      } : current);
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : "素材关联失败");
    } finally {
      setBusyAssetId(null);
    }
  }

  async function unlinkAsset(asset: Asset) {
    if (!project || !selectedSceneId || busyAssetId) return;
    setBusyAssetId(asset.id);
    setWorkspaceError("");
    try {
      const response = await fetch(`/api/screenplays/${project.id}/scenes/${selectedSceneId}/assets/${asset.id}`, {
        method: "DELETE",
        headers: { "X-CSRF-Token": csrf },
      });
      if (response.status === 401) {
        unauthorizedRef.current();
        return;
      }
      if (!response.ok) throw new Error(await parseError(response));
      setProject((current) => current ? {
        ...current,
        scenes: current.scenes.map((scene) => scene.id === selectedSceneId ? { ...scene, assets: scene.assets.filter((item) => item.id !== asset.id) } : scene),
      } : current);
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : "素材取消关联失败");
    } finally {
      setBusyAssetId(null);
    }
  }

  function openAsset(asset: Asset) {
    if (asset.asset_type === "image") onOpenImage(asset);
    else if (asset.asset_type === "video") onOpenVideo(asset);
    else window.open("/api/assets/" + asset.id + "/file/original", "_blank", "noopener,noreferrer");
  }

  const filteredProjects = useMemo(() => {
    const term = projectSearch.trim().toLowerCase();
    if (!term) return projects;
    return projects.filter((item) => `${item.title} ${item.logline}`.toLowerCase().includes(term));
  }, [projectSearch, projects]);

  const selectedScene = project?.scenes.find((scene) => scene.id === selectedSceneId) || null;
  const selectedSceneIndex = project && selectedScene ? project.scenes.findIndex((scene) => scene.id === selectedScene.id) : -1;

  const linkedAssets = selectedScene?.assets || [];
  const linkedIds = new Set(linkedAssets.map((asset) => asset.id));
  const normalizedAssetSearch = assetSearch.trim().toLowerCase();
  const matchesAssetSearch = (asset: Asset) => {
    if (!normalizedAssetSearch) return true;
    return `${asset.title} ${asset.category} ${asset.tags.join(" ")} ${asset.original_name || ""}`
      .toLowerCase()
      .includes(normalizedAssetSearch);
  };
  const visibleLinkedAssets = linkedAssets.filter(matchesAssetSearch);
  const visibleLibraryAssets = libraryAssets.filter((asset) => assetTab === "linked" ? linkedIds.has(asset.id) : true).filter(matchesAssetSearch);
  const displayedAssets = assetTab === "linked"
    ? visibleLinkedAssets.map((asset) => libraryAssets.find((item) => item.id === asset.id) || asset)
    : visibleLibraryAssets;

  if (projectsLoading && projects.length === 0) {
    return <div className="screenplay-loading"><Loader2 className="spin-icon" size={19} /><span>正在打开剧本工作区</span></div>;
  }

  return (
    <div className="screenplay-layout">
      <aside className="screenplay-projects">
        <header className="screenplay-panel-head">
          <div>
            <span className="screenplay-panel-kicker">PROJECTS</span>
            <h2>剧本项目 <span>{projects.length}</span></h2>
          </div>
          <button type="button" className="screenplay-square-button" onClick={() => void openProjectDialog("create")} title="新建剧本项目" aria-label="新建剧本项目">
            <Plus size={17} />
          </button>
        </header>
        <label className="screenplay-search">
          <Search size={14} />
          <input value={projectSearch} onChange={(event) => setProjectSearch(event.target.value)} placeholder="搜索剧本" aria-label="搜索剧本" />
          {projectSearch && <button type="button" onClick={() => setProjectSearch("")} aria-label="清除搜索"><X size={12} /></button>}
        </label>
        <div className="project-list">
          {filteredProjects.map((item) => (
            <button
              key={item.id}
              type="button"
              className={"project-list-item" + (item.id === selectedProjectId ? " is-active" : "")}
              onClick={() => void selectProject(item.id)}
            >
              <span className={"project-status-dot " + item.status} />
              <span className="project-list-copy">
                <strong>{item.title}</strong>
                <small>{item.logline || "尚未填写故事梗概"}</small>
                <span><b>{item.scene_count}</b> 个场景 · {formatShortDate(item.updated_at)}</span>
              </span>
            </button>
          ))}
          {filteredProjects.length === 0 && (
            <div className="project-list-empty">
              <BookOpen size={21} />
              <span>{projects.length === 0 ? "还没有剧本项目" : "没有匹配的剧本"}</span>
              {projects.length === 0 && <button type="button" onClick={() => void openProjectDialog("create")}>创建第一个项目</button>}
            </div>
          )}
        </div>
        <footer className="screenplay-projects-foot">
          <span><span className="status-dot" />仅管理员可见</span>
          <small>{projects.reduce((total, item) => total + item.scene_count, 0)} 个场景已归档</small>
        </footer>
      </aside>

      <section className="screenplay-editor">
        {workspaceError && <div className="screenplay-inline-alert" role="alert"><span>{workspaceError}</span><button type="button" onClick={() => setWorkspaceError("")}><X size={14} /></button></div>}
        {projectLoading && !project ? (
          <div className="screenplay-empty-editor"><Loader2 className="spin-icon" size={22} /><span>正在载入剧本</span></div>
        ) : !project ? (
          <div className="screenplay-empty-editor">
            <span className="screenplay-empty-icon"><BookOpen size={27} /></span>
            <h2>从一个故事开始</h2>
            <p>创建剧本项目后，可以逐场写作并把资产库里的画面、视频和参考资料关联到场景。</p>
            <button className="primary-button" type="button" onClick={() => void openProjectDialog("create")}><Plus size={15} />新建剧本项目</button>
          </div>
        ) : (
          <>
            <header className="screenplay-project-head">
              <div className="screenplay-project-title">
                <span className="screenplay-panel-kicker">SCREENPLAY WORKSPACE</span>
                <div>
                  <h1>{project.title}</h1>
                  <span className={"project-status " + project.status}>{statusLabels[project.status]}</span>
                </div>
                <p>{project.logline || "还没有故事梗概，打开项目设置补充项目方向。"}</p>
              </div>
              <div className="screenplay-project-actions">
                <button type="button" className="secondary-button screenplay-mobile-assets-button" onClick={() => setAssetsPanelOpen(true)}>
                  <Layers3 size={14} />场景素材
                </button>
                <button type="button" className="secondary-button screenplay-settings-button" onClick={() => void openProjectDialog("edit")}>
                  <Settings2 size={14} />项目设置
                </button>
              </div>
            </header>

            <div className="scene-strip-wrap">
              <div className="scene-strip" role="tablist" aria-label="剧本场景">
                {project.scenes.map((scene, index) => (
                  <button
                    key={scene.id}
                    type="button"
                    role="tab"
                    aria-selected={scene.id === selectedSceneId}
                    className={"scene-strip-card" + (scene.id === selectedSceneId ? " is-active" : "")}
                    onClick={() => setSelectedSceneId(scene.id)}
                  >
                    <span className="scene-number">{String(index + 1).padStart(2, "0")}</span>
                    <span>
                      <strong>{scene.heading || "未命名场景"}</strong>
                      <small>{scene.content.length} 字{scene.assets.length ? ` · ${scene.assets.length} 个素材` : ""}</small>
                    </span>
                  </button>
                ))}
                <button className="scene-add-card" type="button" onClick={() => void addScene()} disabled={sceneBusy} title="新增场景">
                  {sceneBusy ? <Loader2 className="spin-icon" size={16} /> : <Plus size={16} />}
                  <span>新增场景</span>
                </button>
              </div>
            </div>

            {selectedScene ? (
              <article className="screenplay-page">
                <div className="scene-editor-toolbar">
                  <span>场景 {String(selectedSceneIndex + 1).padStart(2, "0")} / {String(project.scenes.length).padStart(2, "0")}</span>
                  <div>
                    <button type="button" onClick={() => void moveScene(-1)} disabled={sceneBusy || selectedSceneIndex <= 0} title="前移场景" aria-label="前移场景"><ArrowUp size={14} /></button>
                    <button type="button" onClick={() => void moveScene(1)} disabled={sceneBusy || selectedSceneIndex >= project.scenes.length - 1} title="后移场景" aria-label="后移场景"><ArrowDown size={14} /></button>
                    <button type="button" onClick={() => void deleteScene()} disabled={sceneBusy || project.scenes.length <= 1} title="删除场景" aria-label="删除场景"><Trash2 size={14} /></button>
                  </div>
                </div>
                <label className="scene-heading-field">
                  <span>场景标题 / SLUGLINE</span>
                  <input
                    value={selectedScene.heading}
                    maxLength={300}
                    onChange={(event) => updateScene("heading", event.target.value)}
                    placeholder="EXT. 海面 - 夜"
                  />
                </label>
                <textarea
                  className="screenplay-body"
                  value={selectedScene.content}
                  onChange={(event) => updateScene("content", event.target.value)}
                  onKeyDown={insertIndent}
                  placeholder={"在这里写动作、对白和转场…\n\n动作描述从左侧开始。\n对白可以参考：\n\n角色名\n　　对白内容。"}
                  aria-label="场景剧本内容"
                  spellCheck={false}
                />
                <details className="scene-notes">
                  <summary>制作备注 <span>{selectedScene.notes ? "已填写" : "镜头、声音或执行说明"}</span></summary>
                  <textarea
                    value={selectedScene.notes}
                    onChange={(event) => updateScene("notes", event.target.value)}
                    placeholder="记录拍摄方向、声音设计、参考影片或执行注意事项"
                  />
                </details>
                <footer className="screenplay-page-foot">
                  <div><span>{selectedScene.content.length} 字</span><i />{selectedScene.assets.length > 0 && <><span>{selectedScene.assets.length} 个关联素材</span><i /></>}<span>Tab 缩进</span></div>
                  <SaveIndicator state={saveState} error={saveError} onRetry={() => { setSaveState("pending"); setSaveTick((value) => value + 1); }} />
                </footer>
              </article>
            ) : (
              <div className="screenplay-empty-editor"><span>这个项目还没有场景</span></div>
            )}
          </>
        )}
      </section>

      {assetsPanelOpen && <button type="button" className="scene-assets-backdrop" onClick={() => setAssetsPanelOpen(false)} aria-label="关闭场景素材面板" />}
      <aside className={"scene-assets-panel" + (assetsPanelOpen ? " is-open" : "")}>
        <header className="scene-assets-head">
          <div>
            <span className="screenplay-panel-kicker">SCENE REFERENCES</span>
            <h2><Layers3 size={16} />场景素材</h2>
          </div>
          <span className="linked-count">{linkedAssets.length}</span>
          <button type="button" className="scene-assets-close" onClick={() => setAssetsPanelOpen(false)} aria-label="关闭场景素材面板"><X size={17} /></button>
        </header>
        <p className="scene-assets-context">
          {selectedScene ? <>为「{selectedScene.heading || "未命名场景"}」关联视觉参考</> : "选择一个场景后开始关联素材"}
        </p>
        <label className="screenplay-search asset-search">
          <Search size={14} />
          <input value={assetSearch} onChange={(event) => setAssetSearch(event.target.value)} placeholder="搜索资产库" disabled={!selectedScene} aria-label="搜索场景素材" />
          {assetSearch && <button type="button" onClick={() => setAssetSearch("")} aria-label="清除搜索"><X size={12} /></button>}
        </label>
        <div className="asset-tabs" role="group" aria-label="场景素材筛选">
          <button type="button" className={assetTab === "linked" ? "is-active" : ""} onClick={() => setAssetTab("linked")}>已关联 {linkedAssets.length}</button>
          <button type="button" className={assetTab === "library" ? "is-active" : ""} onClick={() => setAssetTab("library")}>素材库</button>
        </div>
        <div className="scene-asset-list">
          {displayedAssets.map((asset) => {
            const isLinked = linkedIds.has(asset.id);
            const coverUrl = asset.cover_url || (asset.asset_type === "image" ? "/api/assets/" + asset.id + "/file/original" : null);
            return (
              <div className="scene-asset-row" key={asset.id}>
                  <button type="button" className="scene-asset-thumb" onClick={() => openAsset(asset)} title={"预览 " + asset.title}>
                  {coverUrl ? <img src={coverUrl} alt="" loading="lazy" /> : <span><AssetTypeIcon type={asset.asset_type} /></span>}
                  {asset.asset_type === "video" && <i><Video size={10} /></i>}
                </button>
                <button type="button" className="scene-asset-copy" onClick={() => openAsset(asset)}>
                  <strong>{asset.title}</strong>
                  <small><AssetTypeIcon type={asset.asset_type} />{assetTypeLabel(asset.asset_type)} · {asset.category}</small>
                </button>
                {isLinked ? (
                  <button type="button" className="asset-row-action unlink" onClick={() => void unlinkAsset(asset)} disabled={busyAssetId === asset.id} title="取消关联" aria-label={"取消关联 " + asset.title}>
                    {busyAssetId === asset.id ? <Loader2 className="spin-icon" size={14} /> : <X size={14} />}
                  </button>
                ) : (
                  <button type="button" className="asset-row-action link" onClick={() => void linkAsset(asset)} disabled={busyAssetId === asset.id} title="关联到场景" aria-label={"关联 " + asset.title}>
                    {busyAssetId === asset.id ? <Loader2 className="spin-icon" size={14} /> : <Plus size={14} />}
                  </button>
                )}
              </div>
            );
          })}
          {displayedAssets.length === 0 && (
            <div className="scene-assets-empty">
              {assetTab === "linked" ? <Link2 size={22} /> : <Layers3 size={22} />}
              <strong>{assetTab === "linked" ? (assetSearch ? "没有匹配的关联素材" : "还没有关联素材") : (libraryAssets.length === 0 ? "资产库还是空的" : "没有匹配的素材")}</strong>
              <span>{assetTab === "linked" ? "切换到素材库，把画面和参考放进这个场景。" : "先在资产库上传素材，再回来关联。"}</span>
              {assetTab === "linked" && <button type="button" onClick={() => setAssetTab("library")}>浏览素材库</button>}
            </div>
          )}
        </div>
        <footer className="scene-assets-foot"><Link2 size={12} />关联只影响剧本组织，不会移动或修改原资产</footer>
      </aside>

      {dialogMode && (
        <ProjectDialog
          mode={dialogMode}
          project={dialogMode === "edit" ? project || undefined : undefined}
          busy={dialogBusy}
          error={dialogError}
          onClose={() => setDialogMode(null)}
          onSubmit={(values) => void submitProjectDialog(values)}
          onDelete={dialogMode === "edit" ? () => void deleteProject() : undefined}
        />
      )}
    </div>
  );
}
