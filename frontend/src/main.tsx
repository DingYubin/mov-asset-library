import { FormEvent, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Archive,
  AudioLines,
  Check,
  ChevronDown,
  CircleHelp,
  Clapperboard,
  FileImage,
  FileText,
  Filter,
  FolderOpen,
  Grid2X2,
  Image,
  Layers3,
  LogOut,
  MoreHorizontal,
  Plus,
  Search,
  Settings2,
  ShieldCheck,
  Upload,
  Video,
  X,
} from "lucide-react";
import "./styles.css";

type Asset = {
  id: string;
  title: string;
  asset_type: string;
  description: string;
  category: string;
  tags: string[];
  status: "draft" | "published" | "archived";
  visibility: "private" | "public";
  cover_url?: string | null;
  original_name?: string | null;
  mime_type?: string | null;
  size_bytes: number;
  created_at: string;
  updated_at: string;
};

type Session = {
  authenticated: boolean;
  csrf_token?: string | null;
  user?: { id: string; username: string } | null;
};

const categories = ["全部资产", "图片", "视频", "音频", "文档", "未分类"];

function typeLabel(type: string) {
  if (type === "image") return "图片";
  if (type === "video") return "视频";
  if (type === "audio") return "音频";
  if (type === "text" || type === "application") return "文档";
  return "其他";
}

function typeIcon(type: string) {
  if (type === "image") return <Image size={16} />;
  if (type === "video") return <Video size={16} />;
  if (type === "audio") return <AudioLines size={16} />;
  if (type === "application" || type === "text") return <FileText size={16} />;
  return <FileImage size={16} />;
}

function formatBytes(bytes: number) {
  if (bytes >= 1024 * 1024 * 1024) return (bytes / (1024 * 1024 * 1024)).toFixed(1) + " GB";
  if (bytes >= 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  if (bytes >= 1024) return Math.round(bytes / 1024) + " KB";
  return bytes + " B";
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "short", day: "numeric" }).format(new Date(value));
}

async function parseError(response: Response) {
  try {
    const body = await response.json();
    return body.detail || "请求失败 (" + response.status + ")";
  } catch {
    return "请求失败 (" + response.status + ")";
  }
}

function LoginScreen({ onLogin }: { onLogin: (session: Session) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (!response.ok) throw new Error(await parseError(response));
      onLogin(await response.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-panel">
        <div className="brand-mark login-mark"><Layers3 size={19} /></div>
        <h1>登录资产库</h1>
        <p className="login-copy">使用管理员账号继续</p>
        <form onSubmit={submit} className="login-form">
          <label>
            管理员账号
            <input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} required />
          </label>
          <label>
            密码
            <input autoComplete="current-password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} required />
          </label>
          {error && <div className="form-error" role="alert">{error}</div>}
          <button className="primary-button login-submit" disabled={busy}>
            <ShieldCheck size={16} />{busy ? "正在验证…" : "安全登录"}
          </button>
        </form>
        <div className="login-foot"><ShieldCheck size={14} /> 私有工作区 · 会话加密保护</div>
      </section>
      <aside className="login-side">
        <div className="login-art">
          <div className="art-frame art-one" />
          <div className="art-frame art-two" />
          <div className="art-frame art-three" />
          <div className="art-caption"><span>MOV</span><span>CREATIVE ARCHIVE</span></div>
        </div>
        <p>所有创作素材，在这里有序生长。</p>
      </aside>
    </main>
  );
}

function UploadDialog({
  csrf,
  onClose,
  onCreated,
}: {
  csrf: string;
  onClose: () => void;
  onCreated: () => Promise<void> | void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [category, setCategory] = useState("未分类");
  const [tags, setTags] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file) {
      setError("请选择要上传的文件");
      return;
    }
    const form = new FormData();
    form.set("title", title.trim() || file.name.replace(/\.[^.]+$/, ""));
    form.set("category", category);
    form.set("tags", tags);
    form.set("file", file);
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/assets", {
        method: "POST",
        headers: { "X-CSRF-Token": csrf },
        body: form,
      });
      if (!response.ok) throw new Error(await parseError(response));
      await onCreated();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="upload-dialog" role="dialog" aria-modal="true" aria-labelledby="upload-title">
        <header className="dialog-head">
          <div><h2 id="upload-title">添加资产</h2><p>上传文件并补充基础信息</p></div>
          <button className="icon-button" onClick={onClose} aria-label="关闭"><X size={18} /></button>
        </header>
        <form onSubmit={submit}>
          <label className={"drop-zone" + (file ? " has-file" : "")}>
            <input type="file" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
            <span className="drop-icon">{file ? typeIcon(file.type.split("/")[0]) : <Upload size={20} />}</span>
            <strong>{file?.name ?? "选择文件或拖放到这里"}</strong>
            <span>{file ? formatBytes(file.size) : "图片、视频、音频或文档"}</span>
          </label>
          <div className="form-grid">
            <label>资产名称<input value={title} onChange={(event) => setTitle(event.target.value)} placeholder={file?.name.replace(/\.[^.]+$/, "") || "为资产命名"} /></label>
            <label>分类<select value={category} onChange={(event) => setCategory(event.target.value)}><option>未分类</option><option>图片</option><option>视频</option><option>音频</option><option>文档</option><option>品牌素材</option><option>项目素材</option></select></label>
            <label className="full-field">标签<input value={tags} onChange={(event) => setTags(event.target.value)} placeholder="用逗号分隔标签" /></label>
          </div>
          {error && <div className="form-error" role="alert">{error}</div>}
          <footer className="dialog-actions">
            <button type="button" className="secondary-button" onClick={onClose}>取消</button>
            <button className="primary-button" disabled={busy || !file}><Upload size={15} />{busy ? "正在上传…" : "上传资产"}</button>
          </footer>
        </form>
      </section>
    </div>
  );
}

function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [selectedCategory, setSelectedCategory] = useState("全部资产");
  const [statusFilter, setStatusFilter] = useState("全部状态");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [view, setView] = useState<"grid" | "list">("grid");
  const [menuId, setMenuId] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/auth/session")
      .then((response) => response.json())
      .then((data: Session) => setSession(data.authenticated ? data : { authenticated: false }))
      .catch(() => setSession({ authenticated: false }))
      .finally(() => setLoading(false));
  }, []);

  async function loadAssets() {
    setError("");
    const params = new URLSearchParams();
    if (query.trim()) params.set("q", query.trim());
    if (selectedCategory !== "全部资产") params.set("category", selectedCategory);
    if (statusFilter !== "全部状态") params.set("status", statusFilter);
    try {
      const response = await fetch("/api/assets?" + params.toString());
      if (response.status === 401) {
        setSession({ authenticated: false });
        return;
      }
      if (!response.ok) throw new Error(await parseError(response));
      setAssets(await response.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "资产加载失败");
    }
  }

  useEffect(() => {
    if (session?.authenticated) void loadAssets();
  }, [session?.authenticated, query, selectedCategory, statusFilter]);

  const counts = useMemo(() => ({
    all: assets.length,
    images: assets.filter((item) => item.asset_type === "image").length,
    videos: assets.filter((item) => item.asset_type === "video").length,
    audio: assets.filter((item) => item.asset_type === "audio").length,
    docs: assets.filter((item) => ["application", "text"].includes(item.asset_type)).length,
    published: assets.filter((item) => item.status === "published").length,
  }), [assets]);

  async function updateStatus(asset: Asset) {
    if (!session?.csrf_token) return;
    const next = asset.status === "published" ? "draft" : "published";
    const response = await fetch("/api/assets/" + asset.id, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": session.csrf_token },
      body: JSON.stringify({ status: next, visibility: next === "published" ? "public" : "private" }),
    });
    if (!response.ok) {
      setError(await parseError(response));
      return;
    }
    setMenuId(null);
    await loadAssets();
  }

  async function deleteAsset(asset: Asset) {
    if (!session?.csrf_token || !window.confirm("确定删除「" + asset.title + "」及其文件吗？")) return;
    const response = await fetch("/api/assets/" + asset.id, {
      method: "DELETE",
      headers: { "X-CSRF-Token": session.csrf_token },
    });
    if (!response.ok) {
      setError(await parseError(response));
      return;
    }
    setMenuId(null);
    await loadAssets();
  }

  async function logout() {
    if (session?.csrf_token) {
      await fetch("/api/auth/logout", { method: "POST", headers: { "X-CSRF-Token": session.csrf_token } });
    }
    setSession({ authenticated: false });
  }

  if (loading) return <div className="boot-screen"><Layers3 size={22} /><span>正在连接资产库</span></div>;
  if (!session?.authenticated) return <LoginScreen onLogin={setSession} />;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#"><span className="brand-mark"><Layers3 size={17} /></span><span><strong>MOV</strong><small>ASSET LIBRARY</small></span></a>
        <div className="sidebar-section">
          <span className="section-label">工作区</span>
          <button className="nav-item active"><Grid2X2 size={17} />资产总览<span className="nav-count">{counts.all}</span></button>
          <button className="nav-item" onClick={() => { setSelectedCategory("全部资产"); setStatusFilter("全部状态"); }}><FolderOpen size={17} />全部资产</button>
          <button className="nav-item" onClick={() => setStatusFilter("draft")}><Archive size={17} />待整理</button>
          <button className="nav-item" onClick={() => setStatusFilter("published")}><Check size={17} />已发布<span className="nav-count">{counts.published}</span></button>
        </div>
        <div className="sidebar-section category-section">
          <span className="section-label">资产分类<button className="tiny-action" title="分类设置"><Plus size={14} /></button></span>
          {categories.slice(1).map((category) => (
            <button className={"nav-item sub-nav" + (selectedCategory === category ? " active" : "")} key={category} onClick={() => { setSelectedCategory(category); setStatusFilter("全部状态"); }}>
              <span className={"category-dot " + category} />{category}
            </button>
          ))}
        </div>
        <div className="sidebar-bottom">
          <div className="storage-note"><span><span className="status-dot" />私人空间</span><small>文件仅你可访问</small></div>
          <button className="nav-item" title="帮助"><CircleHelp size={17} />帮助与反馈</button>
          <button className="profile-row" onClick={logout}><span className="avatar">{session.user?.username.slice(0, 1).toUpperCase() || "A"}</span><span className="profile-copy"><strong>{session.user?.username || "管理员"}</strong><small>管理员</small></span><LogOut size={16} className="logout-icon" /></button>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar"><div className="breadcrumbs"><span>工作区</span><span className="crumb-slash">/</span><strong>资产总览</strong></div><div className="top-actions"><span className="private-indicator"><ShieldCheck size={14} />私有空间</span><button className="icon-button" title="设置"><Settings2 size={17} /></button></div></header>
        <div className="page-content">
          <div className="page-heading"><div><h1>资产总览</h1><p>集中管理你的创作素材与项目资源</p></div><button className="primary-button" onClick={() => setDialogOpen(true)}><Plus size={17} />添加资产</button></div>
          <section className="overview-strip" aria-label="资产概览">
            <div className="overview-item"><span className="overview-icon total"><Layers3 size={17} /></span><span><small>全部资产</small><strong>{counts.all}</strong></span></div>
            <div className="overview-divider" />
            <div className="overview-item"><span className="overview-icon image"><Image size={17} /></span><span><small>图片</small><strong>{counts.images}</strong></span></div>
            <div className="overview-divider" />
            <div className="overview-item"><span className="overview-icon video"><Clapperboard size={17} /></span><span><small>视频</small><strong>{counts.videos}</strong></span></div>
            <div className="overview-divider" />
            <div className="overview-item"><span className="overview-icon audio"><AudioLines size={17} /></span><span><small>音频与文档</small><strong>{counts.audio + counts.docs}</strong></span></div>
          </section>
          <section className="library-section">
            <div className="library-heading"><div><h2>资产文件</h2><span>{assets.length} 个项目</span></div><button className="filter-button"><Filter size={15} />筛选<ChevronDown size={14} /></button></div>
            <div className="toolbar">
              <div className="search-box"><Search size={16} /><input aria-label="搜索资产" placeholder="搜索名称、标签或描述" value={query} onChange={(event) => setQuery(event.target.value)} /><kbd>⌘ K</kbd></div>
              <div className="toolbar-right"><label className="status-select"><select aria-label="状态筛选" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option>全部状态</option><option value="draft">待整理</option><option value="published">已发布</option><option value="archived">已归档</option></select><ChevronDown size={14} /></label><div className="view-switch" role="group" aria-label="视图模式"><button className={view === "grid" ? "selected" : ""} onClick={() => setView("grid")} title="网格视图"><Grid2X2 size={16} /></button><button className={view === "list" ? "selected" : ""} onClick={() => setView("list")} title="列表视图"><Layers3 size={16} /></button></div></div>
            </div>
            {error && <div className="inline-error" role="alert">{error}</div>}
            {assets.length === 0 ? (
              <div className="empty-state"><span className="empty-icon"><FolderOpen size={24} /></span><h3>{query || selectedCategory !== "全部资产" ? "没有找到匹配的资产" : "资产库还是空的"}</h3><p>{query || selectedCategory !== "全部资产" ? "调整搜索词或筛选条件后再试" : "上传第一份素材，开始整理你的创作资源"}</p>{!query && selectedCategory === "全部资产" && <button className="secondary-button" onClick={() => setDialogOpen(true)}><Upload size={15} />上传第一份资产</button>}</div>
            ) : (
              <div className={"asset-grid" + (view === "list" ? " list-view" : "")}>
                {assets.map((asset) => (
                  <article className="asset-card" key={asset.id}>
                    <a className="asset-preview" href={"/api/assets/" + asset.id + "/file/original"} target="_blank" rel="noreferrer">
                      {asset.asset_type === "image" && asset.cover_url ? <img src={asset.cover_url} alt="" /> : <span className={"file-type-art " + asset.asset_type}>{typeIcon(asset.asset_type)}<small>{typeLabel(asset.asset_type)}</small></span>}
                      <span className={"asset-status " + asset.status}>{asset.status === "published" ? "已发布" : asset.status === "archived" ? "已归档" : "待整理"}</span>
                    </a>
                    <div className="asset-info">
                      <div className="asset-title-row"><a href={"/api/assets/" + asset.id + "/file/original"} target="_blank" rel="noreferrer" className="asset-title">{asset.title}</a><button className="icon-button card-menu-button" onClick={() => setMenuId(menuId === asset.id ? null : asset.id)} title="更多操作"><MoreHorizontal size={18} /></button>{menuId === asset.id && <div className="asset-menu"><button onClick={() => void updateStatus(asset)}>{asset.status === "published" ? "撤回发布" : "发布资产"}</button><button className="danger-action" onClick={() => void deleteAsset(asset)}>删除资产</button></div>}</div>
                      <div className="asset-meta"><span>{typeLabel(asset.asset_type)}</span><i /><span>{formatBytes(asset.size_bytes)}</span><i /><span>{formatDate(asset.updated_at)}</span></div>
                      {asset.tags.length > 0 && <div className="tag-row">{asset.tags.slice(0, 3).map((tag) => <span key={tag}>{tag}</span>)}</div>}
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>
          <footer className="page-footer"><span>© MOV Asset Library</span><span><ShieldCheck size={13} />你的文件保存在私有存储空间</span></footer>
        </div>
      </main>
      {dialogOpen && session.csrf_token && <UploadDialog csrf={session.csrf_token} onClose={() => setDialogOpen(false)} onCreated={loadAssets} />}
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
