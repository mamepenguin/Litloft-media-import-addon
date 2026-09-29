"use client";

import { useCallback } from "react";
import { useTranslations } from "next-intl";
import { RefreshCw } from "lucide-react";

import { ActionMenuItem } from "@/components/ActionMenuItem";
import { useToast } from "@/components/ToastProvider";
import { LOFT_MIME } from "@/lib/playerKind";

import { refreshLoft } from "./api";
import { notifyLoftRefreshed } from "./loftRefresh";

interface Props {
  fileId: string;
  drive: string;
  mimeType?: string | null;
  onRequestClose?: () => void;
}

export default function LoftRefreshMenuItem({
  fileId,
  drive,
  mimeType,
  onRequestClose,
}: Props) {
  const t = useTranslations("mediaImport.loftMetadata");
  const toast = useToast();

  const handleClick = useCallback(async () => {
    onRequestClose?.();
    toast.info(t("refreshStarted"));
    try {
      await refreshLoft(fileId, drive);
      notifyLoftRefreshed(fileId);
    } catch {
      toast.error(t("refreshFailed"));
    }
  }, [fileId, drive, onRequestClose, toast, t]);

  if (mimeType !== LOFT_MIME) return null;

  return (
    <ActionMenuItem icon={RefreshCw} label={t("refresh")} onClick={handleClick} />
  );
}
