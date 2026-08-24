/**
 * Minimal ambient declarations for the UXP host modules the panel uses.
 * Adobe does not publish a types package for these; only the surface this
 * plugin actually touches is declared, so a typo in a call still fails the check.
 */
declare module "uxp" {
  interface Metadata { dateModified?: string | Date; dateCreated?: string | Date; size?: number; }
  interface Entry {
    name: string;
    isFile: boolean;
    isFolder: boolean;
    nativePath?: string;
    getMetadata(): Promise<Metadata>;
  }
  interface File extends Entry {
    read(options?: object): Promise<string>;
    write(data: string, options?: object): Promise<void>;
  }
  interface Folder extends Entry {
    getEntries(): Promise<Entry[]>;
    getEntry(name: string): Promise<Entry & Folder & File>;
    createFile(name: string, options?: { overwrite?: boolean }): Promise<File>;
  }
  interface FileSystem {
    getFolder(options?: { initialDomain?: unknown }): Promise<Folder | null>;
    createPersistentToken(entry: Entry): Promise<string>;
    getEntryForPersistentToken(token: string): Promise<Entry & Folder & File>;
    /** The plugin's own storage. Never requires a permission prompt. */
    getDataFolder(): Promise<Folder>;
    /** Arbitrary path access; requires manifest localFileSystem: "fullAccess". */
    getEntryWithUrl(url: string): Promise<Entry & Folder & File>;
  }
  export const storage: { localFileSystem: FileSystem };
}

declare module "premierepro" {
  export * from "@adobe/premierepro";
  import type { premierepro } from "@adobe/premierepro";
  const api: premierepro & Record<string, any>;
  export = api;
}
