# ADVBOX: customer folders and uploads

How the ADVBOX screen creates a folder in a customer's files and uploads documents into it, and how the connector
(`src/jurisync/connectors/advbox/documents.py`, tools `advbox_create_folder` and `advbox_upload_files`) reproduces it.

Source: two browser recordings (HAR) made by the owner on 2026-11-09. The 19:27 one records the folder flow (open a
lawsuit, create a folder, upload three PDFs into it, then delete them and the folder). The 15:20 one records task
creation with an attachment (`/s3` with `root=posts`), the flow the connector already implements for
`advbox_create_task`; it says nothing new about folders. All values below are placeholders.

## Scope: folders belong to the customer

The file manager opened from a lawsuit (`GET /lawsuits/<lawsuitId>/edit`) is the **customer's** tree: every request
sends `root=customer` and `folder=<customerId>` (the customer the lawsuit page links as `customers/<customerId>/edit`).
The lawsuit ID is not sent. The tools therefore take `customerId`, like `advbox_list_files` / `advbox_save_folder`.
Folders have no numeric ID: they are named by their path (`data-path`), which is also what later requests send.

## 1. List a folder (confirmed)

`POST /file/search`, `application/x-www-form-urlencoded`, `X-Requested-With: XMLHttpRequest`

| field | value |
| --- | --- |
| `_token` | CSRF token |
| `search` | `''` (text to search across all folders, used by the screen's search box) |
| `path_filter` | folder path, `''` for the root |
| `root` | `customer` |
| `folder` | `<customerId>` |
| `order` | `name,asc` |

Answer: HTML, one `<li class="queue sync completed">` per entry.

- folder: `<a class="item folder open-folder[ hide-upload]" data-path="<path>">`, `<p class="name">`,
  `<p class="datetime">N itens • size</p>`, rename box `<input maxlength="40" data-type=… data-item=…>`.
  `hide-upload` marks a system folder ("Anexado em tarefas", the task attachments) where the screen offers no upload;
  the parser now reports it as `uploadable: false`.
- file: `<a href="files/<n>/<storedName>?f=<fileId>" class="item open-new-page">`, `<p class="name">`,
  `<input maxlength="140" data-type="file" data-item="<fileId>">`, `<p class="datetime">date • size</p>`, plus an
  options menu (open, download, copy link, move to folder, rename, delete).

This was already implemented (`AdvboxDocuments.list_customer_files`, `parse_file_tree`).

## 2. Create a folder (confirmed at the root)

`POST /file/create-folder`, form-encoded, headers `X-CSRF-TOKEN` and `X-Requested-With`

| field | value |
| --- | --- |
| `path` | the new folder (at the root: just its name, e.g. `Nova pasta`) |
| `customer` | `<customerId>` |

Answer: `200`, body `success` (plain text). No ID comes back; the screen re-lists the parent (`/file/search`) and the
new folder appears with `data-path` equal to the name.

Connector: `session.post` sends the CSRF token as `_token` in the body instead of the header (Laravel accepts either;
every other ADVBOX write of the connector does the same). Before creating, the parent is listed and an existing folder
with the same name (ignoring case) is returned with `existing: true`; after `success` the parent is listed again to
return the folder.

## 3. Upload files into a folder (confirmed): one request per file

`POST /s3`, `multipart/form-data`, headers `X-CSRF-TOKEN` and `X-Requested-With`. The screen sends one request per
file (three files, three requests, started in parallel).

| part | value |
| --- | --- |
| `file[]` | the file (`filename`, `Content-Type`, e.g. `application/pdf`) |
| `root` | `customer` |
| `folder` | `<customerId>` |
| `path_filter` | target folder path (the folder that is open), e.g. `Nova pasta` |

Answer: `200`, HTML with **one `<li>`**: the stored file, same markup as a listing entry (`data-item="<fileId>"`,
`href="files/<n>/<storedName>?f=<fileId>"`). There is no second "register" call: unlike the task attachments
(`root=posts`, `folder=files/temp/<userId>`, answer = a bare code that `POST /posts` then references), a folder upload
is final as soon as `/s3` answers.

ADVBOX renames the file: `storedName` is the uploaded stem with dots removed, `_<number>` appended, original extension
(e.g. `Doc v1.2.pdf` → `Doc v12_<number>.pdf`). So a same-name check cannot tell whether a file was already
uploaded; the connector reports `storedName` and `fileId` per file.

The three `fileId`s returned by the uploads are the ones the next listing of the folder shows.

## 4. Delete (confirmed; used only for cleanup)

`POST /s3`, form-encoded: `id=<fileId>`, `type=file`, `customer=''`, `_method=DELETE`. Answer: empty `200`.
This is exactly the request `AdvboxClient.discard_staged` already sends for staged task attachments, so the upload
rollback reuses it.

The recording also deletes the folder: `POST /s3` with `id=<folder path>`, `type=folder`, `customer=<customerId>`,
`_method=DELETE`. The connector does not expose folder deletion.

## Connector behaviour

- `advbox_create_folder(customerId, name, dryRun=false)`: name 1–40 characters (the screen's limit), no slash.
  ADVBOX folders are flat (see below), so there is no parent argument. Idempotent by the existing-name check. On a
  lost answer (timeout, 5xx) the customer's files are listed again (a read, not a replay); still absent → error
  saying the folder MAY have been created.
- `advbox_upload_files(customerId, folder, files[], dryRun=false)`: 1–10 names from the shared documents folder
  (`jurisync.core.documents_dir.resolve_saved_document`, same as `attachmentFiles` of `advbox_create_task`), types
  pdf/png/jpg/jpeg/mp3/txt/doc/docx/odt/xls/xlsx, 25 MB each. The folder must exist and not be a system folder. One
  `/s3` per file, in order, under the office lock (`session.exclusive()`); files are read before the lock is taken.
  - clear refusal (4xx) of file *k*: files 1…k-1 of this call are deleted (step 4) → all-or-nothing; the error names
    any file that could not be removed.
  - unknown outcome (timeout/connection error, 5xx, or an answer that does not name one stored file): nothing is
    deleted or replayed; the error says file *k* MAY have been uploaded, lists the confirmed ones (with `fileId`) and
    the ones not sent, and asks to list the folder before retrying.
  - after the uploads the folder is listed once and each file reports `listed: true|false`.
- `createFolder`-and-upload in one call was left out: `advbox_create_folder` is idempotent and cheap, and two tools
  keep the safety rules (idempotent vs not) separate.

## Checked live (2026-11-09, Victor's office, the customer of the recording)

- Create, then the same call again: `created: true`, then `existing: true`, one folder.
- **No subfolders**: `path=<folder>/Sub` answered `success` but created a second ROOT folder literally named
  `<folder>/Sub`; listing `<folder>` showed nothing inside. ADVBOX folders are flat, so the connector no longer
  offers a parent folder.
- Upload one small PDF, then two in one call: each answered with one `<li>`, stored as `<name>_<4 digits>.pdf`,
  `listed: true`, and the folder listing showed the three `fileId`s.
- Delete the three files (`type=file`, `customer=''`) and both folders (`type=folder`, `id=<path>`,
  `customer=<customerId>`): empty `200` each; the listings confirmed nothing was left.

Still assumed:

- Upload limits: 25 MB and the type list are the connector's own; ADVBOX showed none.
- Another answer than `success` / one `<li>` (e.g. a validation message) is treated as a refusal for folders and as an
  unknown outcome for uploads.
- Folder name collation: the existing-name check ignores case; ADVBOX itself may allow `Docs` and `docs` side by side.
