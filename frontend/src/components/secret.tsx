// A box for a key or secret: it can be stored or removed, never read back.
import { useState } from "react";
import { api } from "../lib/api";
import { Button, Field, Input, useToast } from "./ui";

export function SecretField({ name, label, stored, onSaved }: { name: string; label: string; stored: boolean; onSaved: () => void }) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const save = async (next: string) => {
    setBusy(true);
    try {
      await api.put("/settings/secret", { name, value: next });
      setValue("");
      onSaved();
      toast({ tone: "good", title: next ? "Key stored (encrypted)" : "Key removed" });
    } catch (e) {
      toast({ tone: "bad", title: "Could not save the key", body: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Field label={label} hint={stored ? "A key is stored. It is encrypted on this PC and is never shown again." : "Not set."}>
      <div className="flex gap-2">
        <Input type="password" autoComplete="off" value={value} onChange={(event) => setValue(event.target.value)} placeholder={stored ? "••••••••  (stored)" : "Paste the key"} />
        <Button onClick={() => save(value)} busy={busy} disabled={!value.trim()}>Save</Button>
        {stored && <Button variant="ghost" onClick={() => save("")} disabled={busy}>Remove</Button>}
      </div>
    </Field>
  );
}
