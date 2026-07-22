import { useCallback, useEffect, useState } from "react";
import { listCampaignBriefs } from "../api/client";
import type { CampaignBriefRead } from "../api/types";

export function useCampaignBriefs() {
  const [briefs, setBriefs] = useState<CampaignBriefRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const result = await listCampaignBriefs();
      setBriefs(result);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load briefs.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { briefs, loading, error, refresh };
}
