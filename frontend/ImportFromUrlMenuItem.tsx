"use client";

import { useCallback, useState } from "react";
import { useTranslations } from "next-intl";
import { Link as LinkIcon } from "lucide-react";

import { ActionMenuItem } from "@/components/ActionMenuItem";
import { usePolicy } from "@/hooks/usePolicy";

import ImportFromUrlDialog from "./ImportFromUrlDialog";

interface Props {
  drive: string;
  path?: string;
  onRequestClose?: () => void;
  onDialogOpenChange?: (open: boolean) => void;
}

/**
 * The dialog is rendered by this row, which lives inside the host's menu:
 * closing the menu while the dialog is up would unmount both. The menu is
 * asked to close only after an import succeeds; cancelling returns to it.
 */
export default function ImportFromUrlMenuItem({
  drive,
  path = "",
  onRequestClose,
  onDialogOpenChange,
}: Props) {
  const t = useTranslations("mediaImport.importFromUrl");
  const policy = usePolicy(drive, "media_import", "url_import");
  const [open, setOpen] = useState(false);

  const handleOpen = useCallback(() => {
    setOpen(true);
    onDialogOpenChange?.(true);
  }, [onDialogOpenChange]);

  const handleCancel = useCallback(() => {
    setOpen(false);
    onDialogOpenChange?.(false);
  }, [onDialogOpenChange]);

  const handleImported = useCallback(() => {
    setOpen(false);
    onDialogOpenChange?.(false);
    onRequestClose?.();
  }, [onDialogOpenChange, onRequestClose]);

  if (!drive) return null;
  if (!policy.isLoading && !policy.enabled) return null;

  return (
    <>
      <ActionMenuItem icon={LinkIcon} label={t("menuItem")} onClick={handleOpen} />
      {open && (
        <ImportFromUrlDialog
          drive={drive}
          path={path}
          onCancel={handleCancel}
          onImported={handleImported}
        />
      )}
    </>
  );
}
