//! Boot images: FAT32 EFI system partition, GPT disk image (USB/dd) and UEFI ISO.

use std::collections::BTreeMap;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::Path;

use crate::{out_dir, qemu};

const MIB: u64 = 1024 * 1024;

pub fn make_all(esp_dir: &Path, selftest: bool) -> Result<(), String> {
    let suffix = if selftest { "-selftest" } else { "" };
    let out = out_dir();
    let esp_img = out.join(format!("esp{suffix}.img"));
    make_esp(esp_dir, &esp_img)?;
    let disk = out.join(format!("devos{suffix}.img"));
    make_gpt_disk(&esp_img, &disk)?;
    println!("xtask: disk image {}", disk.display());
    if !selftest {
        let iso = out.join("devos.iso");
        make_iso(&esp_img, &iso)?;
        println!("xtask: UEFI ISO {}", iso.display());
    }
    Ok(())
}

fn make_esp(dir: &Path, img: &Path) -> Result<(), String> {
    let size = 64 * MIB;
    let file = OpenOptions::new().read(true).write(true).create(true).truncate(true).open(img)
        .map_err(|e| e.to_string())?;
    file.set_len(size).map_err(|e| e.to_string())?;
    let opts = fatfs::FormatVolumeOptions::new().fat_type(fatfs::FatType::Fat32).volume_label(*b"DEVOS-ESP  ");
    fatfs::format_volume(&file, opts).map_err(|e| format!("format ESP: {e}"))?;
    let fs = fatfs::FileSystem::new(&file, fatfs::FsOptions::new()).map_err(|e| e.to_string())?;
    copy_tree(dir, &fs.root_dir())?;
    Ok(())
}

fn copy_tree<IO: fatfs::ReadWriteSeek>(src: &Path, dst: &fatfs::Dir<IO>) -> Result<(), String> {
    for e in fs::read_dir(src).map_err(|e| e.to_string())? {
        let e = e.map_err(|e| e.to_string())?;
        let name = e.file_name().to_string_lossy().to_string();
        if e.path().is_dir() {
            let sub = dst.create_dir(&name).map_err(|e| e.to_string())?;
            copy_tree(&e.path(), &sub)?;
        } else {
            let mut data = Vec::new();
            File::open(e.path()).and_then(|mut f| f.read_to_end(&mut data)).map_err(|e| e.to_string())?;
            let mut f = dst.create_file(&name).map_err(|e| e.to_string())?;
            f.truncate().map_err(|e| e.to_string())?;
            f.write_all(&data).map_err(|e| e.to_string())?;
        }
    }
    Ok(())
}

fn make_gpt_disk(esp_img: &Path, disk_path: &Path) -> Result<(), String> {
    let esp_len = fs::metadata(esp_img).map_err(|e| e.to_string())?.len();
    let total = esp_len + 4 * MIB;
    let mut disk = OpenOptions::new().read(true).write(true).create(true).truncate(true).open(disk_path)
        .map_err(|e| e.to_string())?;
    disk.set_len(total).map_err(|e| e.to_string())?;
    let mbr = gpt::mbr::ProtectiveMBR::with_lb_size(u32::try_from(total / 512 - 1).unwrap_or(u32::MAX));
    mbr.overwrite_lba0(&mut disk).map_err(|e| format!("protective MBR: {e}"))?;
    let mut gdisk = gpt::GptConfig::default()
        .writable(true)
        .logical_block_size(gpt::disk::LogicalBlockSize::Lb512)
        .create_from_device(disk, None)
        .map_err(|e| format!("GPT: {e}"))?;
    gdisk.update_partitions(BTreeMap::new()).map_err(|e| format!("GPT: {e}"))?;
    let id = gdisk
        .add_partition("EFI System Partition", esp_len, gpt::partition_types::EFI, 0, None)
        .map_err(|e| format!("GPT partition: {e}"))?;
    let start = gdisk.partitions()[&id].bytes_start(gpt::disk::LogicalBlockSize::Lb512)
        .map_err(|e| e.to_string())?;
    let mut disk = gdisk.write().map_err(|e| format!("GPT write: {e}"))?;
    disk.seek(SeekFrom::Start(start)).map_err(|e| e.to_string())?;
    let mut esp = Vec::new();
    File::open(esp_img).and_then(|mut f| f.read_to_end(&mut esp)).map_err(|e| e.to_string())?;
    disk.write_all(&esp).map_err(|e| e.to_string())?;
    Ok(())
}

/// El Torito UEFI ISO (isohybrid, also bootable from USB) built with xorriso.
fn make_iso(esp_img: &Path, iso: &Path) -> Result<(), String> {
    let root = out_dir().join("isoroot");
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(root.join("boot")).map_err(|e| e.to_string())?;
    fs::copy(esp_img, root.join("boot/efi.img")).map_err(|e| e.to_string())?;
    qemu::tools_run(&[
        "xorriso", "-as", "mkisofs", "-R", "-J", "-V", "DEVOS", "-o", "/work/devos.iso",
        "-e", "boot/efi.img", "-no-emul-boot", "-isohybrid-gpt-basdat", "/work/isoroot",
    ])?;
    if !iso.exists() {
        return Err("xorriso did not produce devos.iso".into());
    }
    Ok(())
}
