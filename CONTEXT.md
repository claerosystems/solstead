# Modular Security System

A modular outdoor security product whose customers own and control their data.

## Language

**Controller**:
The central module containing the rechargeable battery and accepting connections from one or more cameras.
_Avoid_: Hub, base station

**Camera**:
A replaceable imaging module that plugs into the controller.

**Solar panel**:
The separate module that supplies solar energy to the system.

**System**:
The controller, solar panel, and connected cameras considered as one security installation.
_Avoid_: Camera, when referring to the entire installation

**Stand-alone**:
A system whose local recording, remote viewing, and alerts remain usable without services operated by the product vendor, including after the vendor ceases operating. Remote features may depend on customer-provided connectivity and customer-controlled services.
_Avoid_: Offline-only, cloud-free

**Identification zone**:
A defined area of the monitored site where recorded footage is intended to provide enough detail for a customer to identify a person or read a vehicle's licence plate.
_Avoid_: Detection zone, when identification detail is required

**Owner**:
The single person with administrative authority over a system, including its settings, access permissions, keys, and additional data authorization.
_Avoid_: Viewer, administrator account

**Viewer**:
A person invited by the owner to watch a system's live video and recordings, with individually revocable access and no administrative authority. Alert delivery and export permission are granted separately by the owner.
_Avoid_: Owner, shared login

**Export**:
The owner deliberately copying footage out of Solstead's control, such as to the phone's photo library or through a share sheet. Exported copies cannot be retracted, protected, or encrypted by Solstead.
_Avoid_: Download, which is fetching footage into the app to watch it

**Protected clip**:
An event recording the owner has selected to retain outside automatic overwrite, subject to the system's limited protected-storage allocation.
_Avoid_: Backup, permanent recording

**Weather-resistant assembly**:
The installed system with connected cables, capped unused ports, and closed service covers, intended to withstand outdoor rain, snow, dust, and hose spray. Submersion is outside its launch exposure scope.
_Avoid_: Waterproof, without a defined exposure or test rating

**Pairing**:
The three values that let one phone reach one controller: its HTTPS address, the owner token, and the SHA-256 fingerprint of the controller's self-signed certificate. Stored only in the phone's Keychain.
_Avoid_: Login, account

**Owner token**:
The random secret generated when a controller is provisioned and sent as a bearer token on every request. Possession of it is owner authority for the bench prototype.
_Avoid_: Password, API key

**Sealed recording**:
A recording encrypted on the controller to the owner's public keys as it is written, in the age file format. Nothing on the controller can decrypt it; a device key or the recovery key can.
_Avoid_: Encrypted backup, when meaning footage stored on the controller

**Device key**:
The private key a phone generates and keeps in its Keychain to open sealed recordings. Only its public half is sent to the controller.
_Avoid_: Owner token (which authorizes requests but decrypts nothing)

**Recovery key**:
A private key generated during setup and saved by the owner outside the phone and controller, as printed text or a file. It opens every sealed recording and backup when all device keys are lost.
_Avoid_: Password, backup code
