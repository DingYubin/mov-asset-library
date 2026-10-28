export type Asset = {
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

export type Session = {
  authenticated: boolean;
  csrf_token?: string | null;
  user?: { id: string; username: string } | null;
};
