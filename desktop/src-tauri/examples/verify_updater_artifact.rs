use std::{env, error::Error, fs};

use base64::{Engine as _, engine::general_purpose::STANDARD};
use minisign_verify::{PublicKey, Signature};

fn main() -> Result<(), Box<dyn Error>> {
    let args = env::args().skip(1).collect::<Vec<_>>();
    if args.len() != 3 {
        return Err("usage: verify_updater_artifact <installer> <signature-file> <base64-public-key>".into());
    }

    let installer = fs::read(&args[0])?;
    let signature_file = fs::read_to_string(&args[1])?;
    let signature_text = String::from_utf8(STANDARD.decode(signature_file.trim())?)?;
    let signature = Signature::decode(&signature_text)?;
    let public_key_text = String::from_utf8(STANDARD.decode(args[2].trim())?)?;
    let public_key = PublicKey::decode(&public_key_text)?;

    public_key.verify(&installer, &signature, false)?;

    let mut tampered = installer.clone();
    let changed_byte = tampered.len() / 2;
    tampered[changed_byte] ^= 1;
    assert!(
        public_key.verify(&tampered, &signature, false).is_err(),
        "tampered installer unexpectedly passed signature verification"
    );

    let public_key_line = public_key_text
        .lines()
        .nth(1)
        .ok_or("decoded public key does not contain its key line")?;
    let mut wrong_key = STANDARD.decode(public_key_line)?;
    if wrong_key.len() != 42 {
        return Err("decoded public key has an unexpected length".into());
    }
    wrong_key[2] ^= 1;
    let wrong_key = PublicKey::from_base64(&STANDARD.encode(wrong_key))?;
    assert!(
        wrong_key.verify(&installer, &signature, false).is_err(),
        "wrong public key unexpectedly passed signature verification"
    );

    println!("valid updater signature accepted; tampered bytes and a wrong public key rejected");
    Ok(())
}
