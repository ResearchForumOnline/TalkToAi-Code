# Microsoft Store submission status (2026-09-23)

TalkToAi Code **has not been submitted** to Microsoft Store. The public 0.3.0
Windows EXE is an unsigned GitHub preview, not a Store package.

## Blocking account step

Partner Center reported that the signed-in account lacks Microsoft Entra
permission to register an application in its tenant. The proposed product name
"TalkToAi Code" showed as available, but **Reserve product name failed**. The
tenant administrator must grant this account the **Application Developer**
role, or enable **App registrations** for its users. We should then retry the
name reservation and verify that a product ID and package identity exist.
Do not assign broader roles or disable tenant security controls just to bypass
this error.
Deleting a different Store product (such as ZERO ONE Desktop) does not grant
Entra app-registration permission and is not a fix for this error.

## Package route

Use an MSIX package for the Store. Microsoft re-signs certified MSIX packages,
so the Store route does not require purchasing a trusted code-signing
certificate. The EXE route requires a trusted Authenticode signature and is
not viable with the current unsigned installer.

Once the product exists, obtain the **exact, case-sensitive** Identity Name
and Publisher from its Partner Center identity page. Do not guess them. The
Store package must contain the actual x64 app payload, appropriate logos and
licence notices, and a Windows.Desktop full-trust manifest. Version has four
numeric parts with a zero fourth part. A Store build must avoid sending users
to the GitHub EXE updater for an in-place MSIX update.

Before upload: build the package with that identity, inspect its manifest and
payload, run Windows App Certification Kit, test installation/launch,
uninstallation and preserved user data in an isolated Windows environment,
and review Store descriptions, screenshots, privacy/support links, age ratings
and pricing. An automated controller test or unpackaged launch is not an MSIX
install test. Only submit when these gates pass and Partner Center shows a
complete submission.

Official Microsoft references:

- [MSIX package requirements](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msix/app-package-requirements)
- [Partner Center app-creation permission troubleshooting](https://learn.microsoft.com/en-us/windows/apps/publish/faq/troubleshoot-app-creation)
- [Manual desktop MSIX packaging](https://learn.microsoft.com/en-us/windows/msix/desktop/desktop-to-uwp-manual-conversion)
- [EXE/MSI package requirements](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msi/app-package-requirements)
