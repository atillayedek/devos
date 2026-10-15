# DevOS — Full Developer Workstation

DevOS, ISO'dan kurulduktan sonra **ilk masaüstü girişinde** geliştirme için hazır gelen,
Debian 13 "trixie" tabanlı, KDE Plasma masaüstlü bir işletim sistemidir. IDE'ler, SDK'lar,
derleyiciler, Android/Flutter araç zinciri, container/VM araçları, AI kodlama araçları ve web
toolchain'leri kurulumun parçasıdır; Developer Center bunları ilk kez kurmak için değil,
**yönetmek, doğrulamak, güncellemek ve onarmak** içindir.

## İki ISO

| ISO | İçerik |
|---|---|
| `DevOS-Full-Online.iso` | Debian arşivindeki tüm geliştirici paketleri + DevOS uygulamaları. Kurulum sırasında internet varsa geri kalan her şey resmi kaynaklarından indirilir. |
| `DevOS-Full-Offline.iso` | Yukarıdakine ek olarak lisansı yeniden dağıtıma izin veren **tüm** araçlar ISO'ya gömülü (Node, Flutter, Rust, Go, .NET, VSCodium, Ollama, Codex/Gemini CLI, kubectl… — bkz. [docs/LICENSING.md](docs/LICENSING.md)). |

Kurulumda (Calamares) **Developer setup** sayfası iki mod sunar:

* **Full Online** — Chrome, VS Code, JetBrains IDE'leri, Android Studio + SDK/NDK/emulator image,
  Claude Code vb. vendor'ların resmi kaynaklarından kurulur (lisansları aynı sayfada listelenir).
  Kullanıcı masaüstüne ulaştığında ortam hazırdır.
* **Offline** — hiçbir şey indirilmez. İlk girişte internet varsa
  *"Developer Environment Setup incomplete"* bildirimi çıkar; tek tıkla (lisans onayından sonra)
  eksik resmi araçlar indirilir.

Kurulum ekranında Wi-Fi panel simgesinden veya masaüstündeki **Network & Wi-Fi** kısayolundan
seçilir (NetworkManager: Ethernet, Wi-Fi 6/6E/7, Bluetooth/USB/iPhone tethering, VPN: WireGuard,
OpenVPN, OpenConnect, L2TP; proxy, DNS, IPv4/IPv6).

## Masaüstündeki DevOS uygulamaları

* **DevOS Developer Center** — `Developer Environment: READY` yalnızca tüm zorunlu sağlık
  kontrolleri gerçekten geçtiğinde; aksi halde `ACTION REQUIRED` ve neyin neden çalışmadığı.
  Araç başına Launch / Repair / Update, servisler (PostgreSQL, MariaDB, Redis, Docker, libvirt,
  Ollama), Git & GitHub, Windows Development Environment.
* **DevOS AI Center** — ajanlar (Claude Code, Codex, Gemini CLI, yerel model), Ollama modelleri.
* **Accounts & AI** — OpenAI, Anthropic, Google, GitHub, GitLab, JetBrains, Microsoft/Azure, Docker,
  Supabase, Firebase/Google Cloud, AWS, Cloudflare. Giriş her zaman sağlayıcının resmi
  OAuth/device-login akışıyla yapılır; durum sağlayıcının kendi CLI'sinden okunur. API anahtarları
  yalnızca sistem keyring'inde (KWallet / Secret Service) tutulur. **Hesap bağlamak zorunlu değildir.**
* **Vibe Coding** — doğal dille anlatılan uygulamayı seçilen ajan `~/Developer/<proje>` altında
  gerçek dosyalarla, gerçek terminalde oluşturur; IDE aynı klasörü açar. Ajanların kendi onay
  mekanizmaları kapatılmaz (riskli işlemler için kullanıcı onayı).
* **Windows Development Environment** — Visual Studio gibi Windows'a özgü araçlar için kullanıcının
  kendi Windows ISO/lisansıyla QEMU/KVM VM (UEFI + Secure Boot + TPM 2.0, VirtIO, SPICE pano ve
  USB yönlendirme, `~/Developer` virtiofs paylaşımı). Visual Studio'nun resmi bootstrapper'ı paylaşılan
  klasöre indirilebilir. Visual Studio Linux'ta native çalışmaz; DevOS bunu iddia etmez.
* **.exe / .msi çift tıklama** — dosya analiz edilir (PE başlıkları, .NET, DirectX/Vulkan, sürücü,
  anti-cheat, bilinen ürünler) ve *Wine compatible / Bottles recommended / Windows VM required*
  önerisiyle çalıştırılır.

## Derleme

Gereksinim: Docker (Linux konteyner), ~40 GB boş disk (Offline için ~70 GB).

```bash
bash scripts/build.sh Online
```

```bash
bash scripts/build.sh Offline
```

Çıktılar: `out/DevOS-Full-<Edition>.iso` ve `.sha256`. Derleme önce birim testlerini çalıştırır.

Diğer araçlar:

```bash
python3 -m devos.provision.cli --resolve-only
```
Tüm vendor indirmelerini (en güncel kararlı sürüm + checksum) hiçbir şey kurmadan doğrular.

```bash
bash scripts/test-iso.sh out/DevOS-Full-Online.iso
```
ISO'yu QEMU'da açar, seri konsol ve ekran görüntüsüyle live masaüstünün geldiğini doğrular.

## Depo yapısı

```
provisioning/tools.yaml   tek doğruluk kaynağı: araçlar, kurulum yöntemi, lisans, sağlık kontrolleri
devos/                    Python paketi (/usr/lib/python3/dist-packages/devos)
  provision/              devos-provision: kurulum/onarım, sürüm çözümleyiciler, checksum doğrulama
  health.py               devos-health: gerçek sağlık kontrolleri, READY / ACTION REQUIRED
  accounts.py             Accounts & AI: sağlayıcılar, gerçek giriş durumu, keyring
  vibe.py                 Vibe Coding çekirdeği
  winapp.py / winvm.py    Windows uygulama analizi / Windows Developer VM
  gui/                    PyQt6 uygulamaları
bin/                      komutlar (/usr/bin)
live-build/               Debian live-build yapılandırması (paket listeleri, hook'lar, Calamares)
branding/                 logo, ikonlar, duvar kâğıdı, Calamares slaytları (SVG)
scripts/                  build.sh, build-iso.sh, check-packages.sh, test-iso.sh
tests/                    pytest
```

Ayrıntılar: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/LICENSING.md](docs/LICENSING.md).
